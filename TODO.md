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

> ブランチ `feature/m2-pdf-structure`。テスト用論文（英語・日本語）では、GROBID ありでもなしでも、本文中の引用をすべて参考文献に対応付けられる（英語 39/39、日本語 46/46）。

- [x] PyMuPDF で文字座標を取得し、文単位に分割（`page`・`bboxes` 付き）
  - ブロックに頼らず、行の位置・字下げ・行間・行末から段落を復元（日本語の「1行＝1ブロック」の PDF に対応）
  - 柱（ヘッダー・フッター）とページ番号の除去、同じ高さの行の結合、図中の化けた文字列の除外
  - 見出しの検出（番号＋太字または大きいフォント、「Abstract」「参考文献」など番号なしの定型見出し）
- [x] 日本語の文分割（句点ベース＋例外処理）とテスト（括弧内、小数点の「．」、「…。」の後、半角の "." を文末にしない）
- [x] 英語の文分割（`pysbd`）
- [x] GROBID でセクション構造と参考文献リストを抽出（consolidation は無効）。見出しはレイアウトからの検出を優先し、ほとんど検出できない場合に GROBID の見出しの座標で補う
- [x] 日本語論文で GROBID が失敗した場合の、高速モデルによる参考文献の構造化（役割 `reference_extraction`）
  - GROBID の誤認識（著者略歴など）を除外し、取りこぼしを参考文献節の本文（ぶら下げインデントで項目に分割）から補う
- [x] 本文中の引用箇所と参考文献の対応付け（`[12]`、`(Smith, 2020)`、`著者（2020）`、`田中・鈴木（2019）`、`2016a/b`）
- [x] テスト用の公開OA論文を用意（英語1本・日本語1本、CC BY 4.0）→ `tests/fixtures/`
- [x] `make test-integration`（GROBID を使う結合テスト、internal_net 内で実行）

既知の制約（M3 以降で必要に応じて対応）：
- 行末のハイフンは常に取り除く（"sequence-" ＋ "to-sequence" → "sequenceto-sequence"）
- 表の中身は段落として復元せず、多くは `fragment` になる（解析の対象外）
- 脚注は本文の段落として扱われる

## M3. 論理・読みやすさチェック

> ブランチ `feature/m3-logic-check`。確認用 CLI：`python -m core.analyze <PDF> [--max-paragraphs N] [--db PATH]`

- [x] 論証構造の抽出（段落単位、高速モデル、並列）
- [x] 段落内チェック：前提と根拠の不一致、飛躍、過大な主張、主述のねじれ、指示語の曖昧さ
- [x] 段落間チェック：要約の並びから接続の断絶・セクションの役割違反
- [x] 自己一貫性（3回の判定の多数決。同じ種類・同じ文の指摘が過半数に出たものを残す）
- [x] 改善案の生成（文意・専門用語を変えない。用語・数値・引用の表記が消えた案、原文と同じ案は除く）
- [x] 指摘のデータモデル（SPEC 3.4）と SQLite への保存（ステータス・却下理由の変更を含む）
- [x] プロンプトを `core/analyze/prompts/` に日英別で配置
- [x] おいとまAPIのリクエスト回数のログ（1リクエスト1行、モデル・役割別の集計、解析ごとに SQLite へ保存）

試行の結果（テスト用の日本語論文の冒頭8段落、2026-10-13）：
- 指摘5件（前提と根拠の不一致1、飛躍2、指示語の曖昧さ2）。いずれも内容は妥当
- おいとまAPIへのリクエスト57回、約13分。思考モデルが出力上限に達して JSON が途切れる再生成が多かったため、`logic_check` の `max_tokens` を 16384 に増やした
- 論文全体（約40〜70段落 × 3回）では数十分かかる見込み。M5 で進捗表示と、確定した指摘からの逐次表示を行う

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
| 2026-10-13 | テスト用論文は、同じ研究の英語版（ACL Anthology）と日本語版（自然言語処理、J-STAGE）を使う（どちらも CC BY 4.0） |
| 2026-10-13 | 見出しはレイアウト（PyMuPDF）からの検出を優先し、GROBID は参考文献と、見出しを検出できない場合の補完に使う（GROBID は日本語論文の見出しを取れなかったため） |
| 2026-10-13 | 自己一貫性は、指摘の種類が同じで対象の文が重なるものを同じ指摘とみなし、判定回数の過半数で採否を決める。M9 の二重チェックまでは、全会一致なら confidence: high とする |
| 2026-10-13 | 指摘の bboxes は SPEC 3.4 の形式にページ番号を加える（文がページをまたぐ場合に備えて） |
| 2026-10-13 | 指摘の evidence に type: manuscript（原稿内の前提・根拠の文）を加える |
| 2026-10-12 | GROBID の consolidation は使わない（外部の Crossref 等に問い合わせるため。core から常に 0 を指定） |

## 保留事項・質問

- 各役割のモデル割り当て（M9 の評価後に確定）
- `multilingual-e5` が おいとま側で 502（モデルの実体がない）。`leak_check` は暫定で `gte-qwen2-7b-it` を使用中。おいとまの管理者に確認する
- `Ternary-Bonsai-2-27B` の位置づけ（二重チェック役か、ゲートウェイの漏えい検査役か）
- `web` は利用者のブラウザの入口として、通常のブリッジ（`ui_net`）にも所属する（SPEC 2.2 のとおり）。nginx は `/api` を core に中継するだけだが、ネットワーク上は `web` から学外に出られる。必要なら、ホストのファイアウォール（DOCKER-USER チェーン）で `ui_net` からの外向き通信を止める案を検討する
- 学外からの利用経路：Cloudflare Tunnel＋アドレス認証を予定。ただし Cloudflare のエッジで TLS が終端されるため、アップロードする論文・解析結果・注釈付きPDFが平文で学外（Cloudflare）を通過する。SPEC 2.1「学外に一切送信しない」と矛盾するため、採用前に要検討（代替：学内VPN＋SSHポートフォワード、エンドツーエンド暗号化のVPN）
- 学外へのアクセスに上位のHTTPプロキシが必要な環境では、`egress_proxy` に `cache_peer` の設定を追加する必要がある（現状は直接接続を前提）
- `oa_fetch` で OA 本文を取得する際、出版社サイトなど許可リスト外のホストにリダイレクトされる場合の扱い（M7 で検討）
