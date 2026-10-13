"""同時リクエスト数の計測（make bench-concurrency）。API は respx で模擬する。"""

import httpx
import respx

from core.llm.bench import format_table, run_level
from core.llm.client import DEFAULT_BASE_URL, OitomaClient
from core.llm.config import ModelsConfig


async def test_run_level_counts_tokens_and_failures() -> None:
    config = ModelsConfig.model_validate(
        {"api": {"max_retries": 0}, "roles": {"x": {"model": "m"}}, "embeddings": {}}
    )
    replies = iter(
        [
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "文"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 100},
                },
            ),
            httpx.Response(500),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "文"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 50},
                },
            ),
        ]
    )
    with respx.mock:
        respx.post(f"{DEFAULT_BASE_URL}/chat/completions").mock(side_effect=lambda _: next(replies))
        async with OitomaClient(config, api_key="k") as client:
            result = await run_level(client, concurrency=3, model="m", max_tokens=64, thinking=None)
    assert (result.ok, result.failed, result.completion_tokens) == (2, 1, 150)
    assert result.throughput > 0
    table = format_table([result])
    assert "| 3 |" in table and "| 2 | 1 | 150 |" in table
