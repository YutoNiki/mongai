# CI と運用中のサーバーへの自動反映

## 全体の流れ

```
PR を作る ──▶ GitHub Actions（CI）：lint・単体・ゲートウェイの検査・E2E・GROBID の結合・コミットメッセージ
                │
main にマージ ──▶ 同じ CI が全部通ったら、その版を deploy/live ブランチに進める
                │
運用中のサーバー ──▶ systemd のタイマーが5分おきに deploy/live を確かめ、
                     実行中の解析が無ければ main に取り込む（scripts/deploy-live.sh）
```

- GitHub からサーバーへは接続しない。サーバー上で GitHub のワークフローを動かすこと（セルフホストランナー）もしない。
  サーバーが自分から `git fetch` するだけなので、論文データや解析結果、サーバーのログが GitHub に送られることはない
- CI は GitHub のサーバーで、リポジトリの内容（テスト用データは公開論文・合成データだけ）に対して実行する。`.env`・おいとまAPI のキー・論文データは使わない
- サーバーが取り込むのは、CI が通った版だけ

## CI（`.github/workflows/ci.yml`）

PR と main への push で実行する。各ジョブは Makefile 経由で、ローカルと同じネットワークなしのコンテナで動く。

| ジョブ | 内容 |
|---|---|
| lint | `make config`、`make lint`（ruff・mypy・eslint・tsc） |
| test | `make test`（Python・フロントエンドの単体テスト）、`make test-security-offline`（ゲートウェイの検査・インジェクション対策） |
| test-e2e | `make test-e2e`（Playwright） |
| test-integration | `make test-integration`（GROBID） |
| commit-msg | PR・push に含まれるコミットのメッセージを `.githooks/commit-msg` で検査（SPEC 10.1） |
| mark-deployable | main への push で上の全部が通ったら、`deploy/live` をその版に進める（早送りだけ） |

CI では実行しないもの（学内のサーバーで実行する）：

- `make test-security` の後半（`tests/security/run.sh`）：実際のネットワーク構成で おいとまAPI・Google・書誌API に繋がるかを確かめるため、学内から実行する必要がある。ネットワーク構成・ゲートウェイ・`searcher` を変えた PR では、これまでどおり手元で通すこと
- `make check-api`：おいとまAPI のキーが必要なため

## 運用中のサーバーへの自動反映（`scripts/deploy-live.sh`）

`make live` で動かしているチェックアウト（ブランチ `main`）で実行する。毎回次のように判断する。

1. 手元の `main` が `origin/deploy/live` より古くなければ何もしない
2. 次のときは取り込まずに終わる（ログに理由を出す）
   - チェックアウトが `main` 以外のブランチ
   - 手元の `main` に `deploy/live` に無いコミットがある、または作業ツリーに未コミットの変更がある
   - 実行中・待機中のジョブ（解析・ルール抽出・専門知識の抽出・提出要件の抽出）がある → 次の回に取り込む
3. 取り込み方を変更の中身で決める
   - スキーマの版（`core/store/schema.py` の `SCHEMA_VERSION`）が上がる：`core`・`worker` を止めてから取り込み、今のソースで移行（`make migrate-live` と同じ。移行の前にバックアップが作られる）してから起動する
   - 依存関係・Dockerfile・compose・`frontend/index.html`・`vite.config.ts` が変わる：取り込んでからイメージを作り直して起動し直す（`make live` と同じ）
   - それ以外：取り込むだけ（ホットリロードで反映される）

ログには版（コミット）と件数だけを出し、論文・解析の内容は出さない。

注意：

- ジョブの有無を確かめてから取り込むまでの数秒の間に解析が始まると、その解析は worker の再起動で失敗になる（もう一度解析すれば良い）
- 移行や起動に失敗した場合は、そのまま止まる（自動で巻き戻さない）。`journalctl --user -u si-nsa-deploy-live` で確認し、手で直す
- 運用中のチェックアウトで直接作業しない（ブランチを切り替えたり、コミットしたりすると取り込みが止まる）。作業は別の worktree で行う

### 有効にする

運用中のチェックアウトで：

```bash
make deploy-live DRY_RUN=1   # 取り込まずに、何をするかだけを表示
make deploy-timer            # 5分おきに実行する systemd（ユーザー）のタイマーを入れて有効にする
loginctl enable-linger $USER # ログアウト中もタイマーを動かす（必要なら）
```

前提：このチェックアウトから `git fetch origin` がパスワードやパスフレーズの入力なしに通ること（systemd から実行するため、SSH の鍵にパスフレーズがあると失敗する。読み取り専用のデプロイキーを使うのが良い）。

確認と停止：

```bash
systemctl --user list-timers si-nsa-deploy-live.timer
journalctl --user -u si-nsa-deploy-live -n 50
make deploy-timer-remove     # 止めて削除する
```

一時的に止めたいとき（デモの最中など）は `systemctl --user stop si-nsa-deploy-live.timer`、再開は `systemctl --user start si-nsa-deploy-live.timer`。
