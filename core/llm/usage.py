"""おいとまAPIの利用回数の集計。

1回の HTTP リクエストごとにログを1行出し、エンドポイント・モデル・役割ごとに回数とトークン数を数える。
ログにはプロンプトや出力の本文を含めない。
"""

import logging
import threading
from dataclasses import dataclass

from pydantic import BaseModel

logger = logging.getLogger("core.llm.usage")


class UsageRow(BaseModel):
    endpoint: str
    model: str
    role: str
    calls: int  # 呼び出しの回数（再試行は1回にまとめる）
    requests: int  # HTTP リクエストの回数（再試行を含む）
    retries: int
    failures: int  # 再試行しても失敗した呼び出しの回数
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


@dataclass
class _Counter:
    calls: int = 0
    requests: int = 0
    retries: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0


class ApiUsage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str, str], _Counter] = {}
        self._sequence = 0

    def _counter(self, endpoint: str, model: str, role: str | None) -> _Counter:
        key = (endpoint, model, role or "-")
        if key not in self._counters:
            self._counters[key] = _Counter()
        return self._counters[key]

    def record_request(
        self,
        *,
        endpoint: str,
        model: str,
        role: str | None,
        attempt: int,
        status: int | None,
        latency_s: float,
        error: str | None = None,
    ) -> None:
        """HTTP リクエスト1回分を記録し、ログに1行出す。attempt は 1 始まり。"""
        with self._lock:
            self._sequence += 1
            seq = self._sequence
            counter = self._counter(endpoint, model, role)
            counter.requests += 1
            counter.latency_s += latency_s
            if attempt == 1:
                counter.calls += 1
            else:
                counter.retries += 1
        logger.info(
            "oitoma_request seq=%d endpoint=%s model=%s role=%s attempt=%d status=%s "
            "latency=%.2fs%s",
            seq,
            endpoint,
            model,
            role or "-",
            attempt,
            status if status is not None else "-",
            latency_s,
            f" error={error}" if error else "",
        )

    def record_failure(self, *, endpoint: str, model: str, role: str | None) -> None:
        with self._lock:
            self._counter(endpoint, model, role).failures += 1

    def record_tokens(
        self,
        *,
        endpoint: str,
        model: str,
        role: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        with self._lock:
            counter = self._counter(endpoint, model, role)
            counter.prompt_tokens += prompt_tokens or 0
            counter.completion_tokens += completion_tokens or 0

    def rows(self) -> list[UsageRow]:
        with self._lock:
            return [
                UsageRow(
                    endpoint=endpoint,
                    model=model,
                    role=role,
                    calls=c.calls,
                    requests=c.requests,
                    retries=c.retries,
                    failures=c.failures,
                    prompt_tokens=c.prompt_tokens,
                    completion_tokens=c.completion_tokens,
                    latency_s=round(c.latency_s, 3),
                )
                for (endpoint, model, role), c in sorted(self._counters.items())
            ]

    @property
    def total_requests(self) -> int:
        with self._lock:
            return sum(c.requests for c in self._counters.values())

    def log_summary(self, label: str = "") -> None:
        rows = self.rows()
        prefix = f"{label} " if label else ""
        logger.info(
            "%soitoma_usage_total requests=%d calls=%d retries=%d failures=%d "
            "prompt_tokens=%d completion_tokens=%d",
            prefix,
            sum(r.requests for r in rows),
            sum(r.calls for r in rows),
            sum(r.retries for r in rows),
            sum(r.failures for r in rows),
            sum(r.prompt_tokens for r in rows),
            sum(r.completion_tokens for r in rows),
        )
        for r in rows:
            logger.info(
                "%soitoma_usage endpoint=%s model=%s role=%s requests=%d calls=%d retries=%d "
                "failures=%d prompt_tokens=%d completion_tokens=%d latency=%.1fs",
                prefix,
                r.endpoint,
                r.model,
                r.role,
                r.requests,
                r.calls,
                r.retries,
                r.failures,
                r.prompt_tokens,
                r.completion_tokens,
                r.latency_s,
            )
