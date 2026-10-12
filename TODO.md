# TODO（門外不出 / MonGAI）

仕様の詳細は `docs/SPEC.md` を参照。タスクを終えるたびにチェックを入れ、決定事項・保留事項を末尾に記録する。

凡例：`[ ]` 未着手 / `[~]` 作業中 / `[x]` 完了 / `[-]` 見送り

ハッカソンのデモで最低限必要なのは M1〜M5 と M7。**M1（セキュリティ）は簡略化しない。**

---

## M1. 基盤とセキュリティ（Docker）

- [ ] リポジトリの初期構成（`.gitignore` に `.env`・`data/private/` を追加）
- [ ] `.claude/settings.json` で `Read(./data/private/**)` と `Read(./.env)` を deny
- [ ] コミットに Claude を共同著者として入れない仕組み（SPEC 10.1）
  - [ ] `.claude/settings.json` の `attribution`（`commit`・`pr` を空文字。仕様を公式ドキュメントで確認）
  - [ ] `.githooks/commit-msg`：共同著者行・Claude/Anthropic の署名・生成元表記・セッションURLを拒否
  - [ ] `make setup` で `core.hooksPath` を設定
  - [ ] フックのテスト
- [ ] `.env.example`（`OITOMA_API_KEY`、ビルド用プロキシ設定）
- [ ] `compose.yaml`：9サービス（web / core / worker / gateway / searcher / redis / grobid / oitoma_proxy / egress_proxy）
- [ ] ネットワーク：`internal_net`・`gateway_net`・`egress_net` を `internal: true`、`ui_net`・`oitoma_net`・`outbound_net` を用意
- [ ] `web` のみ `127.0.0.1:8080` で公開
- [ ] `compose.dev.yaml`（バインドマウント・ホットリロード）、`compose.test.yaml`
- [ ] Dockerfile：ベースイメージのダイジェスト固定、マルチステージ、非root、`read_only`・`cap_drop`・`no-new-privileges`
- [ ] 全サービスの `healthcheck` と `depends_on: service_healthy`
- [ ] ボリューム：`data_private`・`lancedb`・`app_db`・`audit_log`（`searcher` にはマウントしない）
- [ ] Squid 設定：`oitoma_proxy`（おいとまのホストのみ）、`egress_proxy`（書誌APIのみ）
- [ ] `Makefile`：build / up / down / dev / logs / test / test-security / lint / eval / check-api / clean / clean-data
- [ ] `docker compose config` が通ることを確認
- [ ] おいとまAPIの仕様確認（chat completions・embeddings・JSON出力・思考モード）→ `docs/oitoma_api.md`
- [ ] `core/llm/`：おいとまクライアント、トークン予算（32k）、JSONスキーマ強制、リトライ、並列実行
- [ ] `config/models.yaml` の読み込み
- [ ] セキュリティテスト
  - [ ] `core` から外部（example.com）に到達できない
  - [ ] `core` から おいとまAPI には到達できる
  - [ ] `searcher` に `data_private` がマウントされていない
  - [ ] `egress_proxy` が許可外のドメインを拒否する
- [ ] **決定待ち**：`searcher` が おいとまAPIを使う経路（SPEC 7.1 の2案から選ぶ）

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

## 保留事項・質問

- `searcher` から おいとまAPIを使う経路（SPEC 7.1）
- 各役割のモデル割り当て（M9 の評価後に確定）
- `Ternary-Bonsai-2-27B` の位置づけ（二重チェック役か、ゲートウェイの漏えい検査役か）
