"""トークン予算（全モデル 32,768 トークン、SPEC 5.1）。

トークナイザーは使わずに文字種から保守的に見積もる（tiktoken 等は実行時に外部から
エンコーディングを取得するため使わない）。実際の消費量は API の usage で確認できる。
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# 1文字あたりのトークン数の見積もり（実測より多めに取る）
_WIDE_CHAR_TOKENS = 1.0  # 漢字・かな・全角記号
_NARROW_CHAR_TOKENS = 0.34  # ASCII など（英語は約3文字/トークンとみなす）
_MESSAGE_OVERHEAD = 8  # role やチャットテンプレートの区切り
_REQUEST_OVERHEAD = 16

_WIDE = re.compile(
    r"[\u1100-\u11ff\u2e80-\u303f\u3040-\u30ff\u3100-\u31ff\u3400-\u4dbf"
    r"\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)
# 文（区切り記号と後続の空白を含む）。連結すると元のテキストに戻る
_SENTENCE = re.compile(r".*?(?:[。．！？!?]+|\.(?=\s)|\n|$)\s*", re.DOTALL)


def estimate_tokens(text: str) -> int:
    """テキストのトークン数を多めに見積もる。"""
    wide = len(_WIDE.findall(text))
    narrow = len(text) - wide
    return int(wide * _WIDE_CHAR_TOKENS + narrow * _NARROW_CHAR_TOKENS) + 1


def estimate_messages_tokens(messages: Sequence[dict[str, str]]) -> int:
    return _REQUEST_OVERHEAD + sum(
        _MESSAGE_OVERHEAD + estimate_tokens(m.get("content", "")) for m in messages
    )


class PromptTooLongError(ValueError):
    """プロンプトがコンテキスト長に収まらない。呼び出し側で分割すること。"""


@dataclass(frozen=True)
class TokenBudget:
    context_window: int = 32768
    max_output_tokens: int = 4096
    # 見積もり誤差に備えた余裕（入力側の割合）
    safety_ratio: float = 0.10

    @property
    def max_input_tokens(self) -> int:
        usable = self.context_window - self.max_output_tokens
        return int(usable * (1 - self.safety_ratio))

    def fits(self, messages: Sequence[dict[str, str]]) -> bool:
        return estimate_messages_tokens(messages) <= self.max_input_tokens

    def ensure_fits(self, messages: Sequence[dict[str, str]]) -> int:
        used = estimate_messages_tokens(messages)
        if used > self.max_input_tokens:
            raise PromptTooLongError(
                f"入力の見積もり {used} トークンが上限 {self.max_input_tokens} を超えています"
            )
        return used

    def output_tokens_for(self, messages: Sequence[dict[str, str]]) -> int:
        """入力の見積もりに応じて、出力に回せるトークン数を返す（最大 max_output_tokens）。"""
        used = self.ensure_fits(messages)
        return max(1, min(self.max_output_tokens, self.context_window - used))

    def pack(self, items: Iterable[str], fixed_tokens: int) -> list[list[str]]:
        """items（段落など）を、固定部分（システムプロンプト等）と合わせて収まる束に分ける。

        1つで収まらない要素は split_text で文単位に分割してから詰める。
        """
        limit = self.max_input_tokens - fixed_tokens
        if limit <= 0:
            raise PromptTooLongError("固定部分だけで入力の上限を超えています")
        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0
        for item in items:
            pieces = [item] if estimate_tokens(item) <= limit else split_text(item, limit)
            for piece in pieces:
                cost = estimate_tokens(piece) + 1
                if current and current_tokens + cost > limit:
                    batches.append(current)
                    current, current_tokens = [], 0
                current.append(piece)
                current_tokens += cost
        if current:
            batches.append(current)
        return batches


def split_text(text: str, max_tokens: int) -> list[str]:
    """文の区切りで max_tokens 以下に分割する。1文で超える場合は文字数で切る。

    分割結果を連結すると元のテキストに戻る。
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens は正の値にしてください")
    sentences = [s for s in _SENTENCE.findall(text) if s]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        for part in _hard_split(sentence, max_tokens):
            candidate = current + part
            if current and estimate_tokens(candidate) > max_tokens:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, max_tokens: int) -> list[str]:
    if estimate_tokens(text) <= max_tokens:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    cost = 1.0
    for ch in text:
        c = _WIDE_CHAR_TOKENS if _WIDE.match(ch) else _NARROW_CHAR_TOKENS
        if buf and cost + c > max_tokens:
            parts.append("".join(buf))
            buf, cost = [], 1.0
        buf.append(ch)
        cost += c
    if buf:
        parts.append("".join(buf))
    return parts
