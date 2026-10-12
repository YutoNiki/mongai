# TODO（門外不出 / MonGAI）

仕様の詳細は `docs/SPEC.md` を参照。タスクを終えるたびにチェックを入れ、決定事項・保留事項を末尾に記録する。

凡例：`[ ]` 未着手 / `[~]` 作業中 / `[x]` 完了 / `[-]` 見送り

ハッカソンのデモで最低限必要なのは M1〜M5 と M7。**M1（セキュリティ）は簡略化しない。**

---

## M1. 基盤とセキュリティ（Docker）

> ブランチ `feature/m1-foundation`。2026-10-12 に `make config` / `make build` / `make up`（全9サービス healthy）/ `make lint` / `make test-security`（PASS 60 / FAIL 0）を確認済み。

- [x] リポジトリの初期構成（`.gitignore` に `.env`・`data/private/` を追加）
- [x] `.claude/settings.json` で `Read(./data/private/**)` と `Read(./.env)` を deny
- [x] コミットに Claude を共同著者として入れない仕組み（SPEC 10.1）
  - [x] `.claude/settings.json` の `attribution`（`commit`・`pr` を空文字、`sessionUrl: false`。公式ドキュメントで確認済み）
  - [x] `.githooks/commit-msg`：共同著者行・Claude/Anthropic の署名・生成元表記・セッションURLを拒否
  - [x] `make setup` で `core.hooksPath` を設定
  - [x] フックのテスト（`tests/unit/test_commit_msg_hook.py`）
- [x] `.env.example`（`OITOMA_API_KEY`、ビルド用プロキシ設定）
- [x] `compose.yaml`：9サービス（web / core / worker / gateway / searcher / redis / grobid / oitoma_proxy / egress_proxy）
- [x] ネットワーク：`internal_net`・`gateway_net`・`egress_net` を `internal: true`、`ui_net`・`oitoma_net`・`outbound_net` を用意
- [x] `web` のみ `127.0.0.1:8080` で公開
- [x] `compose.dev.yaml`（バインドマウント・ホットリロード）、`compose.test.yaml`
- [x] Dockerfile：ベースイメージのダイジェスト固定、マルチステージ、非root、`read_only`・`cap_drop`・`no-new-privileges`（grobid・Squid も非root＋read_only で起動を確認）
- [x] 全サービスの `healthcheck` と `depends_on: service_healthy`
- [x] ボリューム：`data_private`・`lancedb`・`app_db`・`audit_log`（`searcher` にはマウントしない）
- [x] Squid 設定：`oitoma_proxy`（おいとまのホストのみ）、`egress_proxy`（書誌APIのみ）。どちらも HTTPS の CONNECT のみ
- [x] `Makefile`：setup / build / up / down / dev / logs / test / test-security / lint / eval / check-api / config / clean / clean-data
- [x] `docker compose config` が通ることを確認（`make config`）
- [x] おいとまAPIの仕様確認 → `docs/oitoma_api.md`（`make check-api` で JSON出力・思考モード・埋め込みを確認）
- [x] `core/llm/`：おいとまクライアント、トークン予算（32k）、JSONスキーマ強制、リトライ、並列実行（自己一貫性の多数決を含む）
- [x] `config/models.yaml` の読み込み
- [x] セキュリティテスト（`tests/security/run.sh`）
  - [x] `core` から外部（example.com）に到達できない
  - [x] `core` から おいとまAPI には到達できる
  - [x] `searcher` に `data_private` がマウントされていない
  - [x] `egress_proxy` が許可外のドメインを拒否する
- [x] `searcher` が おいとまAPIを使う経路 → gateway 経由で core 側に照合を依頼する案に決定（M7 で実装）

## M2. PDF構造化

- [ ] PyMuPDF で文字座標を取得し、文単位に分割（`page`・`bboxes` 付き）
- [ ] 日本語の文分割（句点ベース＋例外処理）とテスト
- [ ] 英語の文分割（`pysbd`）
- [ ] GROBID でセクション構造と参考文献リストを抽出
- [ ] 日本語論文で GROBID が失敗した場合の、高速モデルによる参考文献の構造化
- [ ] 本文中の引用箇所と参考文献の対応付け（`[12]`、`(Smith, 2020)`、`著者（2020）`）
- [ ] テスト用の公開OA論文を用意（英語1本・日本語1本）→ `tests/fixtures/`

## M3. 論理・読みやすさチェック

- [ ] 論証構造の抽出（段落単位、高速モデル、並列）
- [ ] 段落内チェック：前提と根拠の不一致、飛躍、過大な主張、主述のねじれ、指示語の曖昧さ
- [ ] 段落間チェック：要約の並びから接続の断絶・セクションの役割違反
- [ ] 自己一貫性（複数回判定の多数決）
- [ ] 改善案の生成（文意・専門用語を変えない）
- [ ] 指摘のデータモデル（SPEC 3.4）と SQLite への保存
- [ ] プロンプトを `core/analyze/prompts/` に日英別で配置

## M4. PDF注釈

- [ ] 種類ごとの注釈（下線・網掛け・波線・枠・点線の枠）
- [ ] 番号ラベルとポップアップ（種類・重大度／指摘／根拠／改善案）
- [ ] 注釈色の白地に対するコントラスト 3:1 以上のテスト
- [ ] 注釈付きPDFのダウンロード

## M5. ダッシュボードと詳細画面

- [ ] デジタル庁カラーパレットの最新値を取得し `palette.ts`・`palette.py` に定義（2026年7月17日更新版）
- [ ] Noto Sans JP・PDF.js・ECharts をバンドル（外部CDNなし）
- [ ] ダッシュボード：総指摘数と重大度別内訳（前回比つき）、カテゴリ別（件数順）、セクション別（論文順）、指摘一覧、メタ情報
- [ ] フィルター（カテゴリ・重大度・セクション・確度）を上部または左部に配置
- [ ] 詳細画面：PDFビューア＋指摘詳細、ダッシュボードから2クリック以内
- [ ] 指摘のステータス変更（対応済み・却下＋理由）
- [ ] 解析の進捗表示と、確定した指摘からの逐次表示
- [ ] アクセシビリティ：グラフの代替テキスト、表形式表示、CSV、キーボード操作
- [ ] 「外部通信ログ」画面

## M6. ルール

- [ ] ルール資料のアップロードとチャンク化
- [ ] ルール抽出（高速モデル、SPEC 3.2 の形式）
- [ ] 重複・言い換えの統合（埋め込み）
- [ ] ルールの確認・編集・承認画面
- [ ] 機械判定（略語の初出定義、図表参照漏れ、単位の表記ゆれ）
- [ ] セクションに応じたルール検索と LLM 判定

## M7. 引用検証

- [ ] `gateway`：3種のスキーマ、原稿との照合、造語の検査、件数制限
- [ ] 運用モード（strict / standard / offline）と承認キュー画面
- [ ] 監査ログ（追記専用）
- [ ] `searcher`：Crossref・OpenAlex・PubMed・arXiv・Semantic Scholar・CiNii Research・J-STAGE
- [ ] 書誌の存在と一致の判定（著者・年・タイトル・巻号のずれ）
- [ ] 引用内容の支持判定（アブストラクト/OA本文/アップロード済みPDF）
- [ ] 引用漏れ候補（addi(c)tion・LOCAL RAG）
- [ ] 未知の用語：学内（direction・addi(c)tion・LOCAL RAG）を先に検索
- [ ] セキュリティテスト
  - [ ] 原稿の文を含む `term_lookup` を拒否
  - [ ] 複数語の `term_lookup` を拒否
  - [ ] 未定義フィールドを持つ要求を拒否
  - [ ] 外部テキスト中のプロンプトインジェクションで指摘が汚染されない

## M8. 研究室知識

- [ ] 過去の査読コメント・回答書・添削メモの取り込み（研究室IDで分離）
- [ ] 研究室ルールの抽出（M6 と同形式、出典を「研究室：過去査読」に）
- [ ] 過去の指摘に類似する箇所の検出

## M9. 二重チェックと評価ハーネス

- [ ] 重大・要修正の指摘を第2モデルで独立判定し、確度を付与
- [ ] 評価データセット（合成、日英、各カテゴリ10件＋対照群）
- [ ] `make eval`：役割×モデルごとの適合率・再現率・F1・平均応答時間
- [ ] 評価結果の比較ページ（ダッシュボードと同じデザイン）

## M10. 仕上げ

- [ ] 再解析時の差分表示
- [ ] CSV / JSON / テキスト要約のエクスポート
- [ ] `docs/design_checklist.md`（デジタル庁チェックリストへの対応状況）
- [ ] `docs/dependencies.md`（依存ライブラリと外部通信の有無）
- [ ] README の最終確認（実データでの運用手順）
- [ ] デモシナリオとデモ用データ（公開論文に問題を仕込んだもの）

---

## 決定事項

| 日付 | 内容 |
|---|---|
| 2026-10-12 | サービス名を「門外不出（MonGAI）」、リポジトリ名を `mongai` とする |
| 2026-10-12 | コミット履歴・PR に Claude を共同著者や生成元として記載しない |
| 2026-10-12 | 全コンポーネントを Docker で実装・実行する |
| 2026-10-12 | おいとまAPIは負荷制限なし（管理者確認済み） |
| 2026-10-12 | 対象論文は日本語・英語の両方 |
| 2026-10-12 | UIはデジタル庁ダッシュボードガイドブックに準拠 |
| 2026-10-12 | トークン数は文字種から保守的に見積もる（tiktoken は実行時に外部からデータを取得するため使わない） |
| 2026-10-12 | `searcher` は おいとまAPIを使わない。検索結果の候補を gateway 経由で core に返し、最も一致する文献の選択は core 側の高速モデルで行う（APIキーを searcher に渡さない、oitoma_proxy を gateway_net に繋がない） |
| 2026-10-12 | プロキシは2つとも HTTPS の CONNECT（443番）だけを許可する |
| 2026-10-12 | `make check-api` の結果から、`api.json_mode: json_schema`、`api.thinking_param: chat_template_kwargs` とする（qwen3.8-27b-it は既定で思考オフ） |
| 2026-10-12 | GROBID の consolidation は使わない（外部の Crossref 等に問い合わせるため。core から常に 0 を指定） |

## 保留事項・質問

- 各役割のモデル割り当て（M9 の評価後に確定）
- `multilingual-e5` が おいとま側で 502（モデルの実体がない）。`leak_check` は暫定で `gte-qwen2-7b-it` を使用中。おいとまの管理者に確認する
- `Ternary-Bonsai-2-27B` の位置づけ（二重チェック役か、ゲートウェイの漏えい検査役か）
- `web` は利用者のブラウザの入口として、通常のブリッジ（`ui_net`）にも所属する（SPEC 2.2 のとおり）。nginx は `/api` を core に中継するだけだが、ネットワーク上は `web` から学外に出られる。必要なら、ホストのファイアウォール（DOCKER-USER チェーン）で `ui_net` からの外向き通信を止める案を検討する
- 学外からの利用経路：Cloudflare Tunnel＋アドレス認証を予定。ただし Cloudflare のエッジで TLS が終端されるため、アップロードする論文・解析結果・注釈付きPDFが平文で学外（Cloudflare）を通過する。SPEC 2.1「学外に一切送信しない」と矛盾するため、採用前に要検討（代替：学内VPN＋SSHポートフォワード、エンドツーエンド暗号化のVPN）
- 学外へのアクセスに上位のHTTPプロキシが必要な環境では、`egress_proxy` に `cache_peer` の設定を追加する必要がある（現状は直接接続を前提）
- `oa_fetch` で OA 本文を取得する際、出版社サイトなど許可リスト外のホストにリダイレクトされる場合の扱い（M7 で検討）
