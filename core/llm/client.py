"""おいとまAPI（OpenAI互換）のクライアント。

- 接続先は おいとまのホストに限定する（CLAUDE.md 最優先ルール1）
- 一時的なエラーは指数バックオフで再試行する
- JSON 出力は Pydantic のスキーマで検証し、失敗したらエラーを伝えて再生成させる
- 自己一貫性（同じ判定を複数回行って多数決）を並列に実行する
- ログにはプロンプトや出力の本文を出さない（モデル名・所要時間・トークン数のみ）
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from core.llm.budget import TokenBudget
from core.llm.config import ModelsConfig, RoleConfig, get_models_config
from core.llm.prompts import Lang, load_prompt
from core.llm.usage import ApiUsage

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ironical.okayama-u.ac.jp/ai/v1"
ALLOWED_HOSTS = frozenset({"ironical.okayama-u.ac.jp"})
RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

Message = dict[str, str]

_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class OitomaError(RuntimeError):
    """おいとまAPIの呼び出しに失敗した。"""


class OitomaHTTPError(OitomaError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"おいとまAPIがエラーを返しました（HTTP {status_code}）：{message}")
        self.status_code = status_code


class JSONOutputError(OitomaError):
    """再試行しても、スキーマに合う JSON が得られなかった。"""


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    reasoning: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0


@dataclass(frozen=True)
class VoteResult[T: BaseModel]:
    winner: T
    votes: int
    total: int
    samples: list[T] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        return self.votes / self.total if self.total else 0.0


def validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(
            f"接続先は おいとまAPI（https://{', '.join(sorted(ALLOWED_HOSTS))}）に限られます"
        )
    return base_url.rstrip("/")


def extract_json(text: str) -> Any:
    """モデルの出力から JSON を取り出す（コードブロックや前後の文を許容する）。"""
    stripped = text.strip()
    fenced = _CODE_FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def split_reasoning(content: str) -> tuple[str, str | None]:
    """本文に <think>…</think> が含まれる場合に、思考部分と回答部分を分ける。"""
    thoughts = [m.strip() for m in _THINK_BLOCK.findall(content)]
    answer = _THINK_BLOCK.sub("", content)
    if "</think>" in answer:  # 開始タグがテンプレート側で付く場合
        head, _, answer = answer.rpartition("</think>")
        thoughts.append(head.strip())
    reasoning = "\n".join(t for t in thoughts if t) or None
    return answer.strip(), reasoning


def default_vote_key(item: BaseModel) -> str:
    return json.dumps(item.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class OitomaClient:
    def __init__(
        self,
        config: ModelsConfig | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config or get_models_config()
        url = validate_base_url(base_url or os.environ.get("OITOMA_BASE_URL") or DEFAULT_BASE_URL)
        key = api_key if api_key is not None else os.environ.get("OITOMA_API_KEY", "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._http = httpx.AsyncClient(
            base_url=url,
            headers=headers,
            timeout=httpx.Timeout(self.config.api.timeout_seconds, connect=10.0),
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(self.config.api.max_concurrency)
        self._sleep = sleep
        # リクエスト回数・トークン数の集計（ログにも1リクエスト1行で出す）
        self.usage = ApiUsage()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self.usage.total_requests:
            self.usage.log_summary()
        await self._http.aclose()

    # ------------------------------------------------------------------ HTTP

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        role: str | None = None,
    ) -> Any:
        attempts = self.config.api.max_retries + 1
        model = str(payload.get("model", "-")) if payload else "-"
        endpoint = path.strip("/")

        def record(attempt: int, status: int | None, started: float, error: str | None) -> None:
            self.usage.record_request(
                endpoint=endpoint,
                model=model,
                role=role,
                attempt=attempt + 1,
                status=status,
                latency_s=time.monotonic() - started,
                error=error,
            )

        for attempt in range(attempts):
            started = time.monotonic()
            try:
                async with self._semaphore:
                    started = time.monotonic()
                    response = await self._http.request(method, path, json=payload)
            except httpx.TransportError as exc:
                record(attempt, None, started, type(exc).__name__)
                if attempt + 1 >= attempts:
                    self.usage.record_failure(endpoint=endpoint, model=model, role=role)
                    raise OitomaError(f"おいとまAPIに接続できません：{type(exc).__name__}") from exc
                await self._backoff(attempt)
                continue
            record(attempt, response.status_code, started, None)
            if response.status_code in RETRY_STATUSES and attempt + 1 < attempts:
                await self._backoff(attempt)
                continue
            if response.status_code >= 400:
                self.usage.record_failure(endpoint=endpoint, model=model, role=role)
                raise OitomaHTTPError(response.status_code, _error_message(response))
            return response.json()
        raise OitomaError("再試行の上限に達しました")  # pragma: no cover

    async def _backoff(self, attempt: int) -> None:
        delay = min(30.0, 2.0**attempt) * (0.5 + random.random() / 2)
        await self._sleep(delay)

    # ------------------------------------------------------------------ chat

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/models")
        return [m["id"] for m in data.get("data", [])]

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        response_format: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> ChatResult:
        budget = TokenBudget(
            context_window=self.config.context_window,
            max_output_tokens=max_tokens or self.config.api.default_max_tokens,
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": budget.output_tokens_for(messages),
            "stream": False,
        }
        if thinking is not None and self.config.api.thinking_param == "chat_template_kwargs":
            payload["chat_template_kwargs"] = {"enable_thinking": thinking}
        if response_format is not None:
            payload["response_format"] = response_format

        started = time.monotonic()
        data = await self._request("POST", "/chat/completions", payload, role=role)
        latency = time.monotonic() - started

        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OitomaError("おいとまAPIの応答の形式が想定と異なります") from exc
        content, inline_reasoning = split_reasoning(message.get("content") or "")
        reasoning = message.get("reasoning_content") or message.get("reasoning") or inline_reasoning
        usage = data.get("usage") or {}
        result = ChatResult(
            content=content,
            model=data.get("model", model),
            reasoning=reasoning,
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_s=latency,
        )
        self.usage.record_tokens(
            endpoint="chat/completions",
            model=model,
            role=role,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        logger.debug(
            "chat model=%s role=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s finish=%s",
            result.model,
            role or "-",
            latency,
            result.prompt_tokens,
            result.completion_tokens,
            result.finish_reason,
        )
        return result

    async def chat_role(self, role: str, messages: Sequence[Message]) -> ChatResult:
        cfg = self.config.role(role)
        return await self.chat(messages, role=role, **self._role_kwargs(cfg))

    @staticmethod
    def _role_kwargs(cfg: RoleConfig) -> dict[str, Any]:
        return {
            "model": cfg.model,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "thinking": cfg.thinking,
        }

    def _response_format(self, schema: type[BaseModel]) -> dict[str, Any] | None:
        mode = self.config.api.json_mode
        if mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    async def chat_json[T: BaseModel](
        self,
        role: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        lang: Lang = "ja",
        max_attempts: int = 3,
    ) -> T:
        """役割の設定で呼び出し、出力を schema で検証して返す。

        検証に失敗したら、エラー内容を伝えて再生成させる。
        """
        cfg = self.config.role(role)
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        conversation = _with_system_suffix(
            messages, load_prompt("json_output", lang, schema=schema_text)
        )
        last_error = ""
        for _ in range(max_attempts):
            result = await self.chat(
                conversation,
                response_format=self._response_format(schema),
                role=role,
                **self._role_kwargs(cfg),
            )
            try:
                return schema.model_validate(extract_json(result.content))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:1000]
                logger.warning(
                    "JSON の検証に失敗しました（role=%s finish=%s）。再生成します",
                    role,
                    result.finish_reason,
                )
                conversation = [
                    *conversation,
                    {"role": "assistant", "content": result.content},
                    {"role": "user", "content": load_prompt("json_retry", lang, error=last_error)},
                ]
        raise JSONOutputError(f"role={role}：{max_attempts} 回試してもスキーマに合いません")

    async def chat_json_vote[T: BaseModel](
        self,
        role: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        lang: Lang = "ja",
        key: Callable[[T], str] = default_vote_key,
    ) -> VoteResult[T]:
        """自己一貫性：役割の samples 回だけ並列に判定し、key が一致するものの多数決を取る。"""
        cfg = self.config.role(role)
        n = cfg.samples if cfg.vote == "majority" else 1
        outcomes = await asyncio.gather(
            *(self.chat_json(role, messages, schema, lang=lang) for _ in range(n)),
            return_exceptions=True,
        )
        samples = [o for o in outcomes if isinstance(o, BaseModel)]
        if not samples:
            errors = [o for o in outcomes if isinstance(o, BaseException)]
            raise JSONOutputError(f"role={role}：全 {n} 回の判定に失敗しました") from errors[0]
        counts = Counter(key(s) for s in samples)
        best_key, votes = counts.most_common(1)[0]
        winner = next(s for s in samples if key(s) == best_key)
        return VoteResult(winner=winner, votes=votes, total=n, samples=samples)

    # ------------------------------------------------------------------ embeddings

    async def embed(self, texts: Sequence[str], *, role: str) -> list[list[float]]:
        """埋め込みを返す。1つのインデックスには1つの役割（＝1つのモデル）だけを使うこと。"""
        cfg = self.config.embedding(role)
        batches = [
            list(texts[i : i + cfg.batch_size]) for i in range(0, len(texts), cfg.batch_size)
        ]
        results = await asyncio.gather(*(self._embed_batch(cfg.model, b, role) for b in batches))
        return [vec for batch in results for vec in batch]

    async def _embed_batch(self, model: str, texts: list[str], role: str) -> list[list[float]]:
        data = await self._request(
            "POST", "/embeddings", {"model": model, "input": texts}, role=f"embedding:{role}"
        )
        usage = data.get("usage") or {}
        self.usage.record_tokens(
            endpoint="embeddings",
            model=model,
            role=f"embedding:{role}",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=None,
        )
        items = sorted(data["data"], key=lambda d: d["index"])
        if len(items) != len(texts):
            raise OitomaError("埋め込みの件数が入力と一致しません")
        return [item["embedding"] for item in items]


def _with_system_suffix(messages: Sequence[Message], suffix: str) -> list[Message]:
    result = [dict(m) for m in messages]
    if result and result[0].get("role") == "system":
        result[0]["content"] = f"{result[0]['content']}\n\n{suffix}"
    else:
        result.insert(0, {"role": "system", "content": suffix})
    return result


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        message = body.get("error", {}).get("message") or body.get("detail") or ""
    except (ValueError, AttributeError):
        message = ""
    return str(message)[:300] or response.reason_phrase
