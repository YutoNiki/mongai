"""config/models.yaml の読み込みと、モデルIDの直書き禁止の確認。"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.llm.config import ModelsConfig, load_models_config

ROOT = Path(__file__).resolve().parents[2]


def test_repository_config_loads() -> None:
    config = load_models_config(ROOT / "config" / "models.yaml")
    assert config.context_window == 32768
    for role in (
        "structure_extraction",
        "rule_extraction",
        "logic_check",
        "rule_check",
        "citation_support",
        "suggestion",
        "second_opinion",
        "searcher_match",
    ):
        assert config.role(role).model
    assert config.role("logic_check").samples == 3
    assert config.role("logic_check").vote == "majority"
    assert config.embedding("retrieval").model
    assert config.embedding("leak_check").model


def test_unknown_role_has_clear_error() -> None:
    config = load_models_config(ROOT / "config" / "models.yaml")
    with pytest.raises(KeyError, match="no_such_role"):
        config.role("no_such_role")


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(
            {"roles": {"x": {"model": "m", "temprature": 0.1}}, "embeddings": {}}
        )


def test_env_var_selects_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("roles: {a: {model: m1}}\nembeddings: {}\n", encoding="utf-8")
    monkeypatch.setenv("MONGAI_MODELS_CONFIG", str(path))
    assert load_models_config().role("a").model == "m1"


def test_no_model_ids_hardcoded_in_code() -> None:
    config = load_models_config(ROOT / "config" / "models.yaml")
    ids = {r.model for r in config.roles.values()} | {e.model for e in config.embeddings.values()}
    pattern = re.compile("|".join(re.escape(i) for i in ids))
    offenders = [
        str(path.relative_to(ROOT))
        for package in ("core", "gateway", "searcher")
        for path in (ROOT / package).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
