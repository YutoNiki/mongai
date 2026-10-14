# 依存ライブラリと外部通信

SPEC 2.5・10 に従い、追加したライブラリ・イメージについて、テレメトリや外部通信の有無を記録する。
いずれのコンテナも、外に出られるのはプロキシ経由で許可した宛先だけ（`make test-security` で検証）なので、仮にライブラリが外部通信を試みても学外には届かない。それでも、通信しないものを選び、必要な設定で無効化しておく。

共通の設定（`docker/python/Dockerfile`）：`DO_NOT_TRACK=1`、`HF_HUB_OFFLINE=1`、`HF_HUB_DISABLE_TELEMETRY=1`、`TRANSFORMERS_OFFLINE=1`、`ANONYMIZED_TELEMETRY=False`、`PIP_DISABLE_PIP_VERSION_CHECK=1`

## Python（`requirements/*.txt`、バージョン固定）

| ライブラリ | 版 | 使う場所 | テレメトリ・外部通信 |
|---|---|---|---|
| fastapi | 0.143.0 | core / gateway / searcher | なし |
| uvicorn | 0.54.0 | core / gateway / searcher | なし |
| pydantic | 2.14.1 | 全体 | なし（logfire 連携は未インストール） |
| httpx | 0.28.1 | core / gateway / searcher | なし。環境変数 `HTTPS_PROXY` に従う（compose.yaml で各サービスのプロキシを明示している） |
| pyyaml | 6.0.3 | core / gateway | なし |
| arq | 0.28.0 | worker | なし（Redis にだけ接続） |
| watchfiles | 1.1.0 | core / worker | なし（MIT。ファイルの変更をローカルで監視するだけ）。`make live` で worker をプロセスごと再起動するのに使う |
| redis | 5.3.1 | worker | なし。arq 0.28 が `redis<6` を要求するため 5 系に固定 |
| pymupdf | 1.28.2 | core / worker | なし。**ライセンスは AGPL-3.0**（または Artifex の商用ライセンス）。学内サービスとしてネットワーク越しに提供する場合、AGPL によりソースコードを利用者に提供する義務がある（本リポジトリを公開していれば満たせる）。SPEC 7 で指定されたライブラリ |
| pysbd | 0.3.4 | core / worker | なし（MIT）。Python 3.12 で正規表現リテラルの SyntaxWarning が出るが動作に影響しない（pytest では抑制） |
| cryptography | 50.0.2 | core / worker / authn / devauth（開発のみ） | なし（Apache-2.0 / BSD）。認証サービスが付けた主張の署名（Ed25519）の検証・署名に使う。依存の cffi も通信しない |
| authlib | 1.8.0 | authn | なし（BSD-3-Clause）。テレメトリなし。OIDC の state・nonce・PKCE の値の生成、認可の URL の組み立て、ID トークンのクレームの検証（iss・aud・exp・iat・nonce・azp・at_hash）に使う。自分では通信しない（Google との通信は authn が httpx で行い、`auth_proxy` 経由で Google の OIDC のエンドポイントにだけ届く）。1.8 で非推奨になった httpx 連携（`authlib.integrations.httpx_client`）と `authlib.jose` は使わない。1.9.0 は公開直後のため採らず、2週間以上たった 1.8.0 に固定 |
| joserfc | 1.7.5 | authn | なし（BSD-3-Clause）。authlib 1.8 の依存。Google の公開鍵（JWKS）による ID トークンの署名（RS256）の検証に使う。通信しない |
| pytest / pytest-asyncio / respx | 9.1.1 / 1.4.0 / 0.23.1 | テストのみ | なし。テストは `network_mode: none` のコンテナで実行 |
| ruff / mypy / types-PyYAML | 0.17.0 / 2.4.0 / 6.0.12.20260906 | lint のみ | なし |

| python-multipart | 0.0.32 | core | なし（FastAPI のファイルアップロードに必要） |

## フロントエンド（`frontend/package.json`、版を固定。公開から2週間以上たった版を使う）

すべてビルド時にバンドルし、実行時に外部から読み込むものは無い（nginx の CSP でも禁止）。
`frontend/.npmrc` で npm の更新通知・寄付の案内・監査情報の送信を止めている。

| パッケージ | 版 | 用途 | テレメトリ・外部通信 |
|---|---|---|---|
| react / react-dom | 19.3.0 | UI | なし |
| echarts | 6.1.0 | グラフ（必要な部品だけを読み込む） | なし |
| pdfjs-dist | 6.3.289 | PDF ビューア | なし。文字マップ・標準フォント・WebAssembly・ICC プロファイルを `dist/pdfjs/` に含めて自前で配信。WebAssembly のため CSP に `'wasm-unsafe-eval'` を加えている |
| @fontsource/noto-sans-jp | 5.3.0 | フォント（セルフホスト） | なし |
| typescript | 5.9.3 | 型検査 | なし（typescript-eslint が 6.1 未満を要求するため 5 系） |
| vite / @vitejs/plugin-react | 8.3.1 / 6.1.1 | ビルド | なし。plugin-react 6 の任意の peer 依存の解決で npm が失敗するため `legacy-peer-deps` を使う |
| eslint / @eslint/js / typescript-eslint / eslint-plugin-react-hooks / globals | 10.11.0 / 10.0.1 / 8.71.0 / 7.1.1 / 17.12.0 | lint | なし |
| vitest | 5.0.2 | テスト | なし |
| @playwright/test | 1.63.0 | E2E テスト（`make test-e2e`） | なし（テレメトリなし）。通常はインストール時にブラウザを Microsoft の CDN から取得するが、`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` と `--ignore-scripts` で取得させず、下のイメージに入っているブラウザを使う。テストは `network_mode: none` のコンテナで実行 |
| @types/node / @types/react / @types/react-dom | 22.20.4 / 19.3.0 / 19.3.0 | 型定義 | なし |

### 採用しなかったもの

| ライブラリ | 理由 |
|---|---|
| tiktoken | 初回実行時にエンコーディングをインターネット（openaipublic.blob.core.windows.net）から取得するため。トークン数は `core/llm/budget.py` で保守的に見積もる |
| openai（公式SDK） | httpx で十分なため依存を増やさない。SDK 自体にテレメトリはない |

## コンテナイメージ（タグとダイジェストで固定）

| イメージ | 使う場所 | テレメトリ・外部通信 |
|---|---|---|
| `python:3.12.15-slim` | core / worker / gateway / searcher / authn | なし。実行イメージから pip を削除 |
| `nginxinc/nginx-unprivileged:1.30.5-alpine` | web | なし。CSP で外部リソースの読み込みを禁止 |
| `redis:8.10.2-alpine` | redis | なし。永続化なし |
| `grobid/grobid:0.9.1-crf` | grobid | なし（`core/ingest/grobid.py` で `consolidateHeader`・`consolidateCitations`・`consolidateFunders` を常に `0` にしている）。ただし **書誌の consolidation（`consolidateHeader` / `consolidateCitations`）を有効にすると Crossref 等に問い合わせる**ため、core からは常に `0` を指定する（internal_net なので外には届かないが、タイムアウト待ちになる） |
| `node:22.23.3-bookworm-slim` | web のビルド段階のみ（実行イメージには含めない） | なし。npm の更新通知・監査を無効化 |
| `mcr.microsoft.com/playwright:v1.63.0-noble` | E2E テストのみ（`compose.test.yaml` の `e2e`。ネットワークなし） | なし。Chromium などのブラウザ入り。実行時はネットワークに繋がないので、ブラウザの更新確認なども外に届かない |
| `ubuntu/squid:6.6-24.04_edge` | oitoma_proxy / egress_proxy / auth_proxy | なし。キャッシュ無効、ログは標準出力 |
