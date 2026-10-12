"""gateway の設定（config/gateway.yaml）。"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeakCheck(_Strict):
    similarity_threshold: float = Field(default=0.80, gt=0, le=1)
    min_substring: int = Field(default=12, ge=4)
    embedding_role: str = "leak_check"


class Limits(_Strict):
    max_requests_per_doc: int = Field(default=300, ge=0)


class GatewayConfig(_Strict):
    mode: Literal["strict", "standard", "offline"] = "standard"
    leak_check: LeakCheck = LeakCheck()
    limits: Limits = Limits()


def load_config(path: str | Path | None = None) -> GatewayConfig:
    resolved = Path(path or os.environ.get("GATEWAY_CONFIG") or "/app/config/gateway.yaml")
    if not resolved.exists():
        return GatewayConfig()
    return GatewayConfig.model_validate(yaml.safe_load(resolved.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def get_config() -> GatewayConfig:
    return load_config()
