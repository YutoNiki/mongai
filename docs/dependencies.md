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
| redis | 5.3.1 | worker | なし。arq 0.28 が `redis<6` を要求するため 5 系に固定 |
| pymupdf | 1.28.2 | core / worker | なし。**ライセンスは AGPL-3.0**（または Artifex の商用ライセンス）。学内サービスとしてネットワーク越しに提供する場合、AGPL によりソースコードを利用者に提供する義務がある（本リポジトリを公開していれば満たせる）。SPEC 7 で指定されたライブラリ |
| pysbd | 0.3.4 | core / worker | なし（MIT）。Python 3.12 で正規表現リテラルの SyntaxWarning が出るが動作に影響しない（pytest では抑制） |
| pytest / pytest-asyncio / respx | 9.1.1 / 1.4.0 / 0.23.1 | テストのみ | なし。テストは `network_mode: none` のコンテナで実行 |
| ruff / mypy / types-PyYAML | 0.17.0 / 2.4.0 / 6.0.12.20260906 | lint のみ | なし |

### 採用しなかったもの

| ライブラリ | 理由 |
|---|---|
| tiktoken | 初回実行時にエンコーディングをインターネット（openaipublic.blob.core.windows.net）から取得するため。トークン数は `core/llm/budget.py` で保守的に見積もる |
| openai（公式SDK） | httpx で十分なため依存を増やさない。SDK 自体にテレメトリはない |

## コンテナイメージ（タグとダイジェストで固定）

| イメージ | 使う場所 | テレメトリ・外部通信 |
|---|---|---|
| `python:3.12.15-slim` | core / worker / gateway / searcher | なし。実行イメージから pip を削除 |
| `nginxinc/nginx-unprivileged:1.30.5-alpine` | web | なし。CSP で外部リソースの読み込みを禁止 |
| `redis:8.10.2-alpine` | redis | なし。永続化なし |
| `grobid/grobid:0.9.1-crf` | grobid | なし（`core/ingest/grobid.py` で `consolidateHeader`・`consolidateCitations`・`consolidateFunders` を常に `0` にしている）。ただし **書誌の consolidation（`consolidateHeader` / `consolidateCitations`）を有効にすると Crossref 等に問い合わせる**ため、core からは常に `0` を指定する（internal_net なので外には届かないが、タイムアウト待ちになる） |
| `ubuntu/squid:6.6-24.04_edge` | oitoma_proxy / egress_proxy | なし。キャッシュ無効、ログは標準出力 |
