"""`config/models.yaml` の読み込み（SPEC 5.3）。モデルIDはここ経由でだけ参照する。"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiConfig(_Strict):
    json_mode: Literal["json_schema", "json_object", "none"] = "none"
    thinking_param: Literal["chat_template_kwargs", "none"] = "none"
    timeout_seconds: float = Field(default=300, gt=0)
    max_retries: int = Field(default=4, ge=0)
    max_concurrency: int = Field(default=16, ge=1)
    default_max_tokens: int = Field(default=4096, ge=1)


class RoleConfig(_Strict):
    model: str
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    samples: int = Field(default=1, ge=1)
    vote: Literal["majority"] | None = None
    # 多数決の結果が決まった時点で残りの判定を送らない（core/analyze/voting.py）
    early_stop: bool = False
    thinking: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1)


class EmbeddingConfig(_Strict):
    model: str
    batch_size: int = Field(default=32, ge=1)


class ModelsConfig(_Strict):
    context_window: int = Field(default=32768, ge=1024)
    api: ApiConfig = ApiConfig()
    roles: dict[str, RoleConfig]
    embeddings: dict[str, EmbeddingConfig]

    def role(self, name: str) -> RoleConfig:
        try:
            return self.roles[name]
        except KeyError:
            raise KeyError(f"config/models.yaml に役割 '{name}' がありません") from None

    def embedding(self, name: str) -> EmbeddingConfig:
        try:
            return self.embeddings[name]
        except KeyError:
            raise KeyError(f"config/models.yaml に埋め込みの役割 '{name}' がありません") from None


def load_models_config(path: str | Path | None = None) -> ModelsConfig:
    """設定ファイルを読み込む。

    パスの既定値は環境変数 MONGAI_MODELS_CONFIG、なければリポジトリ内の config/models.yaml。
    """
    resolved = Path(path or os.environ.get("MONGAI_MODELS_CONFIG") or DEFAULT_CONFIG_PATH)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return ModelsConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_models_config() -> ModelsConfig:
    return load_models_config()
