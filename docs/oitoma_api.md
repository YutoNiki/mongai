# おいとまAPI の仕様メモ

SPEC 5.1 に従い、実際に叩いて確認した内容を記録する。APIキーが必要な項目は `make check-api` の出力を「確認結果」に転記し、`config/models.yaml` の `api` 節に反映する。

## 基本

| 項目 | 内容 | 確認方法 |
|---|---|---|
| ベースURL | `https://ironical.okayama-u.ac.jp/ai/v1` | 2026-10-12 確認 |
| 形式 | OpenAI 互換（公式の `openai` Python SDK で `base_url` を指定して利用できる） | 利用者から共有された呼び出し例 |
| 認証 | `Authorization: Bearer <APIキー>` | 2026-10-12 確認（キーなしで下記のエラー） |
| ストリーミング | `stream=True` に対応 | 利用者から共有された呼び出し例 |
| 負荷制限 | なし | 管理者確認済み |
| 名前解決 | 学内DNSで `10.2.17.246`（学内アドレス） | 2026-10-12 確認 |

キーなし・誤ったキーのときの応答（HTTP 401）：

```json
{"error": {"message": "Invalid or missing API key. Please provide a valid API key via 'Authorization: Bearer <key>' header.",
           "type": "invalid_request_error", "param": null, "code": "invalid_api_key"}}
```

## `GET /models`（キーなしでも 200）

2026-10-12 時点の一覧。全モデルに `context_window: 32768` が付いている。

| モデルID | SPEC 5.2 の種類 |
|---|---|
| `qwen3.8-27b-it` | 高品質・思考モデル |
| `qwen3.6-35b-a3b-it` | 高速応答モデル（MoE） |
| `Ternary-Bonsai-2-27B` | 27B（3値量子化） |
| `qwen3-embedding-8b` | 埋め込み |
| `gte-qwen2-7b-it` | 埋め込み |
| `multilingual-e5` | 埋め込み |
| `whisper` | 音声認識（未使用） |
| `flux.2-klein-4b` | 画像生成（未使用） |

`GET /models` はキーなしで応答するため、`make test-security` の「core から oitoma_proxy 経由で おいとまAPIに到達できる」の確認に使っている（キーを使わずに済む）。

## 要確認（APIキーが必要）

`make check-api` で以下を自動的に確認する。送るのは合成した短い文だけ。

| 項目 | 確認内容 | 反映先 |
|---|---|---|
| chat completions | 非ストリームの応答形式、`usage` の有無 | `core/llm/client.py` |
| ストリーミング | チャンク数、`stream_options.include_usage` の対応 | — |
| JSON出力の強制 | `response_format` の `json_schema` / `json_object` が使えるか、出力が正しいか | `config/models.yaml` の `api.json_mode` |
| 思考モード | `chat_template_kwargs.enable_thinking` で切り替えられるか、思考内容が `reasoning_content` / `<think>` タグのどちらで返るか | `api.thinking_param` |
| 埋め込み | 各埋め込みモデルの次元数、`usage` | LanceDB のスキーマ（M6） |

`api.json_mode` を `none` にしても、プロンプトでの指示＋Pydantic での検証＋再生成で JSON を強制できる（API が `response_format` に対応しない場合の退避先）。

## 確認結果（2026-10-12、`make check-api`）

| 項目 | 結果 | 反映 |
|---|---|---|
| chat completions | 200。`message` のキーは `content`・`reasoning`・`refusal`・`tool_calls` など（OpenAI 互換）。`usage` に `prompt_tokens`・`completion_tokens`・`total_tokens` あり。`qwen3.6-35b-a3b-it` で約0.3秒 | — |
| ストリーミング | 200。`stream_options.include_usage` に対応（最後のチャンクに `usage`） | — |
| JSON出力の強制 | `response_format` の `json_schema`・`json_object` とも 200 で、スキーマどおりの出力 | `api.json_mode: json_schema` |
| 思考モード | `qwen3.8-27b-it` は**既定で思考オフ**。`chat_template_kwargs: {"enable_thinking": true}` で思考し、思考内容は `message.reasoning` に入る（`<think>` タグは本文に出ない）。`completion_tokens` には思考分も含まれる | `api.thinking_param: chat_template_kwargs` |
| 埋め込み `qwen3-embedding-8b` | 200、4096 次元 | — |
| 埋め込み `gte-qwen2-7b-it` | 200、3584 次元 | — |
| 埋め込み `multilingual-e5` | **502**：「すべてのembeddingサーバーが応答しませんでした: The model `multilingual-e5` does not exist.」。`/models` の一覧には載っているが、実体がない | `leak_check` を暫定で `gte-qwen2-7b-it` に変更。管理者に確認する |

注意：思考モードでは思考分も `max_tokens` を消費する。思考を使う役割では、`config/models.yaml` の `max_tokens` を大きめにすることを検討する（M3 で調整）。
