"""おいとまクライアントの単体テスト（HTTP は respx でモックし、外部には出ない）。"""

import json
import logging
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from core.llm.client import (
    DEFAULT_BASE_URL,
    JSONOutputError,
    OitomaClient,
    OitomaError,
    OitomaHTTPError,
    extract_json,
    split_reasoning,
)
from core.llm.config import ModelsConfig

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"
EMBED_URL = f"{DEFAULT_BASE_URL}/embeddings"


def make_config(**api: Any) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "context_window": 32768,
            "api": {"max_retries": 2, **api},
            "roles": {
                "fast": {"model": "fast-model", "temperature": 0.0, "thinking": False},
                "judge": {
                    "model": "think-model",
                    "temperature": 0.2,
                    "samples": 3,
                    "vote": "majority",
                    "thinking": True,
                },
            },
            "embeddings": {"retrieval": {"model": "embed-model", "batch_size": 2}},
        }
    )


def chat_response(content: str, **message: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "fast-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content, **message},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def client() -> OitomaClient:
    return OitomaClient(make_config(), api_key="test-key", sleep=no_sleep)


class Verdict(BaseModel):
    label: str
    score: int


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://ironical.okayama-u.ac.jp/ai/v1",
        "https://ironical.okayama-u.ac.jp.evil.example/ai/v1",
        "https://api.openai.com/v1",
    ],
)
def test_rejects_non_oitoma_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="おいとま"):
        OitomaClient(make_config(), base_url=url, api_key="k")


@respx.mock
async def test_chat_sends_key_and_parses(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(return_value=chat_response("こんにちは"))
    result = await client.chat([{"role": "user", "content": "hi"}], model="fast-model")
    assert result.content == "こんにちは"
    assert result.prompt_tokens == 10
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "fast-model"
    assert body["stream"] is False
    assert body["max_tokens"] == 4096
    assert "chat_template_kwargs" not in body  # thinking_param: none


@respx.mock
async def test_thinking_param_injected_when_enabled() -> None:
    client = OitomaClient(
        make_config(thinking_param="chat_template_kwargs"), api_key="k", sleep=no_sleep
    )
    route = respx.post(CHAT_URL).mock(return_value=chat_response("ok"))
    await client.chat_role("judge", [{"role": "user", "content": "x"}])
    body = json.loads(route.calls.last.request.content)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["model"] == "think-model"
    assert body["temperature"] == 0.2


@respx.mock
async def test_reasoning_separated(client: OitomaClient) -> None:
    respx.post(CHAT_URL).mock(return_value=chat_response("<think>考える</think>\n答え"))
    result = await client.chat([{"role": "user", "content": "x"}], model="m")
    assert result.content == "答え"
    assert result.reasoning == "考える"


@respx.mock
async def test_reasoning_field(client: OitomaClient) -> None:
    respx.post(CHAT_URL).mock(return_value=chat_response("答え", reasoning_content="思考"))
    result = await client.chat([{"role": "user", "content": "x"}], model="m")
    assert result.reasoning == "思考"


@respx.mock
async def test_retries_transient_errors(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            chat_response("ok"),
        ]
    )
    result = await client.chat([{"role": "user", "content": "x"}], model="m")
    assert result.content == "ok"
    assert route.call_count == 3


@respx.mock
async def test_gives_up_after_max_retries(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(OitomaHTTPError) as info:
        await client.chat([{"role": "user", "content": "x"}], model="m")
    assert info.value.status_code == 500
    assert route.call_count == 3


@respx.mock
async def test_transport_error_after_retries(client: OitomaClient) -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(OitomaError, match="接続できません"):
        await client.chat([{"role": "user", "content": "x"}], model="m")


@respx.mock
async def test_does_not_retry_client_errors(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid API key"}})
    )
    with pytest.raises(OitomaHTTPError, match="Invalid API key"):
        await client.chat([{"role": "user", "content": "x"}], model="m")
    assert route.call_count == 1


@respx.mock
async def test_chat_json_retries_until_valid(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            chat_response("すみません、JSONではありません"),
            chat_response('{"label": "leap"}'),
            chat_response('```json\n{"label": "leap", "score": 2}\n```'),
        ]
    )
    verdict = await client.chat_json("fast", [{"role": "user", "content": "x"}], Verdict)
    assert verdict == Verdict(label="leap", score=2)
    assert route.call_count == 3
    last = json.loads(route.calls.last.request.content)["messages"]
    assert last[0]["role"] == "system"
    assert '"score"' in last[0]["content"]  # スキーマがシステムプロンプトに入る
    assert last[-1]["role"] == "user"
    assert "スキーマ" in last[-1]["content"]


@respx.mock
async def test_chat_json_gives_up(client: OitomaClient) -> None:
    respx.post(CHAT_URL).mock(return_value=chat_response("not json"))
    with pytest.raises(JSONOutputError):
        await client.chat_json("fast", [{"role": "user", "content": "x"}], Verdict, max_attempts=2)


@respx.mock
async def test_response_format_json_schema() -> None:
    client = OitomaClient(make_config(json_mode="json_schema"), api_key="k", sleep=no_sleep)
    route = respx.post(CHAT_URL).mock(return_value=chat_response('{"label":"a","score":1}'))
    await client.chat_json("fast", [{"role": "user", "content": "x"}], Verdict, lang="en")
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "Verdict"


@respx.mock
async def test_majority_vote(client: OitomaClient) -> None:
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            chat_response('{"label": "leap", "score": 2}'),
            chat_response('{"label": "ok", "score": 0}'),
            chat_response('{"label": "leap", "score": 2}'),
        ]
    )
    result = await client.chat_json_vote("judge", [{"role": "user", "content": "x"}], Verdict)
    assert route.call_count == 3
    assert result.winner == Verdict(label="leap", score=2)
    assert result.votes == 2
    assert result.total == 3
    assert result.agreement == pytest.approx(2 / 3)


@respx.mock
async def test_vote_with_custom_key(client: OitomaClient) -> None:
    respx.post(CHAT_URL).mock(
        side_effect=[
            chat_response('{"label": "leap", "score": 1}'),
            chat_response('{"label": "leap", "score": 3}'),
            chat_response('{"label": "ok", "score": 0}'),
        ]
    )
    result = await client.chat_json_vote(
        "judge", [{"role": "user", "content": "x"}], Verdict, key=lambda v: v.label
    )
    assert result.winner.label == "leap"
    assert result.votes == 2


@respx.mock
async def test_embeddings_batched_and_ordered(client: OitomaClient) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [
            {"index": i, "embedding": [float(len(t))]} for i, t in reversed(list(enumerate(texts)))
        ]
        return httpx.Response(200, json={"data": data})

    route = respx.post(EMBED_URL).mock(side_effect=respond)
    vectors = await client.embed(["a", "bb", "ccc", "dddd", "eeeee"], role="retrieval")
    assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert route.call_count == 3
    assert json.loads(route.calls[0].request.content)["model"] == "embed-model"


@respx.mock
async def test_prompt_content_not_logged(
    client: OitomaClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "未発表の主張：新規手法XYZは既存手法より優れる"
    respx.post(CHAT_URL).mock(return_value=chat_response(f"出力に{secret}"))
    with caplog.at_level(logging.DEBUG):
        await client.chat([{"role": "user", "content": secret}], model="m")
    assert secret not in caplog.text
    assert "test-key" not in caplog.text


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('結果は次のとおりです。{"a": 1} 以上です。') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("JSON なし")


def test_split_reasoning_without_open_tag() -> None:
    answer, reasoning = split_reasoning("考え中…</think>回答")
    assert answer == "回答"
    assert reasoning == "考え中…"
