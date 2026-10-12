"""トークン予算（32k）の見積もりと分割。"""

import pytest

from core.llm.budget import PromptTooLongError, TokenBudget, estimate_tokens, split_text


def test_estimates_are_conservative() -> None:
    # 日本語は1文字1トークン、英語は約3文字1トークンとして多めに見積もる
    assert estimate_tokens("論文の前提と根拠") >= 8
    assert estimate_tokens("a" * 300) >= 100
    assert estimate_tokens("") == 1


def test_budget_limits() -> None:
    budget = TokenBudget(context_window=32768, max_output_tokens=4096, safety_ratio=0.1)
    assert budget.max_input_tokens == int((32768 - 4096) * 0.9)
    ok = [{"role": "user", "content": "短い"}]
    assert budget.fits(ok)
    assert budget.output_tokens_for(ok) == 4096
    too_long = [{"role": "user", "content": "長" * 40000}]
    assert not budget.fits(too_long)
    with pytest.raises(PromptTooLongError):
        budget.ensure_fits(too_long)


@pytest.mark.parametrize(
    "text",
    [
        "これは一文目です。これは二文目です！三文目？" * 50,
        "First sentence. Second sentence! Third one? " * 50,
        "改行で\n区切られた\n文章\n" * 50,
        "句点のない非常に長い文" * 200,
    ],
)
def test_split_text_preserves_text_and_limit(text: str) -> None:
    chunks = split_text(text, 100)
    assert "".join(chunks) == text
    assert all(estimate_tokens(c) <= 100 for c in chunks)
    assert len(chunks) > 1


def test_pack_groups_items_within_limit() -> None:
    budget = TokenBudget(context_window=2048, max_output_tokens=512, safety_ratio=0.0)
    paragraphs = ["段落" * 100 for _ in range(20)]
    batches = budget.pack(paragraphs, fixed_tokens=200)
    limit = budget.max_input_tokens - 200
    assert sum(len(b) for b in batches) == 20
    assert all(sum(estimate_tokens(p) + 1 for p in b) <= limit for b in batches)


def test_pack_splits_oversized_item() -> None:
    budget = TokenBudget(context_window=2048, max_output_tokens=512, safety_ratio=0.0)
    huge = "長い文です。" * 1000
    batches = budget.pack([huge], fixed_tokens=0)
    assert "".join(p for b in batches for p in b) == huge
    assert len(batches) > 1


def test_pack_rejects_fixed_part_over_limit() -> None:
    budget = TokenBudget(context_window=2048, max_output_tokens=512)
    with pytest.raises(PromptTooLongError):
        budget.pack(["x"], fixed_tokens=10_000)
