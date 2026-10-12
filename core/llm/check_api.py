"""おいとまAPIの疎通と機能の確認（make check-api）。

合成した短いプロンプトだけを送り、次を確認して結果を表示する（APIキーは表示しない）。
- モデル一覧、chat completions（非ストリーム / ストリーム）、usage の有無
- JSON 出力の強制（response_format の json_schema / json_object）
- 思考モードの切り替え（chat_template_kwargs.enable_thinking）と、思考内容の返り方
- 埋め込み（config/models.yaml の各埋め込みモデルの次元数）

結果は docs/oitoma_api.md の「確認結果」に転記し、config/models.yaml の api 節を更新する。
"""

import asyncio
import json
import os
import sys
import time
from typing import Any

import httpx

from core.llm.client import DEFAULT_BASE_URL, extract_json, split_reasoning, validate_base_url
from core.llm.config import get_models_config

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}, "unit": {"type": "string"}},
    "required": ["answer", "unit"],
    "additionalProperties": False,
}
PROBE_QUESTION = "りんごが3個とみかんが4個あります。果物は全部で何個ですか。"


async def _chat(
    http: httpx.AsyncClient, model: str, extra: dict[str, Any], content: str = PROBE_QUESTION
) -> tuple[int, dict[str, Any], float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1024,
        "temperature": 0.0,
        **extra,
    }
    started = time.monotonic()
    response = await http.post("/chat/completions", json=payload)
    elapsed = time.monotonic() - started
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:300]}
    return response.status_code, body, elapsed


def _message(body: dict[str, Any]) -> dict[str, Any]:
    try:
        message = body["choices"][0]["message"]
        return message if isinstance(message, dict) else {}
    except (KeyError, IndexError, TypeError):
        return {}


def _json_ok(body: dict[str, Any]) -> bool:
    content, _ = split_reasoning(_message(body).get("content") or "")
    try:
        data = extract_json(content)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("answer") == 7


def _thinking_summary(body: dict[str, Any]) -> dict[str, Any]:
    message = _message(body)
    raw = message.get("content") or ""
    _, inline = split_reasoning(raw)
    field = next((k for k in ("reasoning_content", "reasoning") if message.get(k)), None)
    usage = body.get("usage") or {}
    return {
        "reasoning_field": field,
        "think_tag_in_content": "<think>" in raw or "</think>" in raw,
        "has_reasoning": bool(field or inline),
        "completion_tokens": usage.get("completion_tokens"),
        "completion_tokens_details": usage.get("completion_tokens_details"),
    }


async def run() -> dict[str, Any]:
    config = get_models_config()
    base_url = validate_base_url(os.environ.get("OITOMA_BASE_URL") or DEFAULT_BASE_URL)
    key = os.environ.get("OITOMA_API_KEY", "")
    report: dict[str, Any] = {"base_url": base_url, "api_key_set": bool(key)}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    fast = config.role("structure_extraction").model
    thinker = config.role("logic_check").model

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180) as http:
        response = await http.get("/models")
        models = response.json().get("data", []) if response.status_code == 200 else []
        report["models"] = {
            "status": response.status_code,
            "ids": [m.get("id") for m in models],
            "context_window": {m.get("id"): m.get("context_window") for m in models},
        }
        configured = {r.model for r in config.roles.values()} | {
            e.model for e in config.embeddings.values()
        }
        report["models"]["missing_configured"] = sorted(configured - set(report["models"]["ids"]))
        if not key:
            report["error"] = "OITOMA_API_KEY が設定されていません（.env を確認）"
            return report

        status, body, elapsed = await _chat(http, fast, {})
        report["chat"] = {
            "model": fast,
            "status": status,
            "latency_s": round(elapsed, 2),
            "usage": body.get("usage"),
            "message_keys": sorted(_message(body)),
            "error": body.get("error"),
        }

        chunks = 0
        usage_in_stream = False
        payload = {
            "model": fast,
            "messages": [{"role": "user", "content": PROBE_QUESTION}],
            "max_tokens": 64,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with http.stream("POST", "/chat/completions", json=payload) as resp:
            stream_status = resp.status_code
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunks += 1
                    if json.loads(line[6:]).get("usage"):
                        usage_in_stream = True
        report["stream"] = {
            "model": fast,
            "status": stream_status,
            "chunks": chunks,
            "usage_in_stream": usage_in_stream,
        }

        json_instruction = (
            f"{PROBE_QUESTION}\n次の JSON スキーマに従う JSON だけを出力してください。\n"
            f"{json.dumps(PROBE_SCHEMA, ensure_ascii=False)}"
        )
        formats: dict[str, dict[str, Any]] = {
            "json_schema": {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "probe", "schema": PROBE_SCHEMA},
                }
            },
            "json_object": {"response_format": {"type": "json_object"}},
            "none": {},
        }
        report["json_mode"] = {}
        for name, extra in formats.items():
            status, body, elapsed = await _chat(http, fast, extra, json_instruction)
            report["json_mode"][name] = {
                "status": status,
                "valid": status == 200 and _json_ok(body),
                "latency_s": round(elapsed, 2),
                "error": body.get("error"),
            }

        report["thinking"] = {"model": thinker}
        variants: dict[str, dict[str, Any]] = {
            "default": {},
            "enable_thinking_true": {"chat_template_kwargs": {"enable_thinking": True}},
            "enable_thinking_false": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        for name, extra in variants.items():
            status, body, elapsed = await _chat(http, thinker, extra)
            report["thinking"][name] = {
                "status": status,
                "latency_s": round(elapsed, 2),
                "error": body.get("error"),
                **_thinking_summary(body),
            }

        report["embeddings"] = {}
        for role, emb in config.embeddings.items():
            response = await http.post(
                "/embeddings", json={"model": emb.model, "input": ["門外不出", "peer review"]}
            )
            data = response.json() if response.status_code == 200 else {}
            vectors = [d.get("embedding", []) for d in data.get("data", [])]
            report["embeddings"][role] = {
                "model": emb.model,
                "status": response.status_code,
                "count": len(vectors),
                "dim": len(vectors[0]) if vectors else None,
                "usage": data.get("usage"),
            }
    return report


def main() -> int:
    report = asyncio.run(run())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("chat", {}).get("status") == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
