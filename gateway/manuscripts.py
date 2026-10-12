"""原稿の照合情報（本文の文・タイトル・新規性の主張・造語）。

解析の開始時に core から登録される。学外に出す要求に原稿の内容が混ざっていないかを確かめるために使う。
ディスクには書かず、メモリ上だけに持つ（gateway が再起動したら消え、未登録の論文の要求は拒否する）。
"""

import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

ALLOWED_HOSTS = frozenset({"ironical.okayama-u.ac.jp"})
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACES.sub("", unicodedata.normalize("NFKC", text)).lower()


class ManuscriptGuard(BaseModel):
    """core → gateway の登録内容。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    title: str | None = Field(default=None, max_length=500)
    body: list[str] = Field(default_factory=list, max_length=5000)  # 本文の文（参考文献を除く）
    novelty_claims: list[str] = Field(default_factory=list, max_length=200)
    coined_terms: list[str] = Field(default_factory=list, max_length=200)


@dataclass
class RegisteredManuscript:
    guard: ManuscriptGuard
    body_normalized: str
    claim_vectors: list[list[float]] = field(default_factory=list)


class EmbeddingClient:
    """おいとまAPIの埋め込み（oitoma_proxy 経由）。gateway には core のコードを入れないため最小限で持つ。"""

    def __init__(self, role: str = "leak_check") -> None:
        base = (
            os.environ.get("OITOMA_BASE_URL") or "https://ironical.okayama-u.ac.jp/ai/v1"
        ).rstrip("/")
        parts = urlsplit(base)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ValueError("埋め込みの接続先は おいとまAPI に限られます")
        self.base = base
        self.key = os.environ.get("OITOMA_API_KEY", "")
        config_path = os.environ.get("MONGAI_MODELS_CONFIG", "/app/config/models.yaml")
        with open(config_path, encoding="utf-8") as f:
            self.model = str(yaml.safe_load(f)["embeddings"][role]["model"])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        async with httpx.AsyncClient(timeout=60, headers=headers) as http:
            response = await http.post(
                f"{self.base}/embeddings", json={"model": self.model, "input": texts}
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda d: d["index"])
        return [list(d["embedding"]) for d in data]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class ManuscriptRegistry:
    def __init__(self) -> None:
        self._docs: dict[str, RegisteredManuscript] = {}

    def register(self, guard: ManuscriptGuard, claim_vectors: list[list[float]]) -> None:
        self._docs[guard.doc_id] = RegisteredManuscript(
            guard=guard,
            body_normalized="\n".join(normalize(t) for t in guard.body),
            claim_vectors=claim_vectors,
        )

    def get(self, doc_id: str) -> RegisteredManuscript | None:
        return self._docs.get(doc_id)

    def remove(self, doc_id: str) -> None:
        self._docs.pop(doc_id, None)
