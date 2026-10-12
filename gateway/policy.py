"""学外に出す要求の検査（SPEC 2.3 の 2〜4）。1つでも不合格なら送信しない。

1. スキーマ検証は shared.schemas（未定義フィールドの拒否、用語は1語であること等）
2. 原稿との照合：term・title 以外のフィールドに原稿本文の文字列が含まれていない。
   title は原稿自身のタイトルではない。term は「新規性に関わる主張」と埋め込みが似ていない
3. 新出語の検査：term が原稿で定義された造語・手法名ではない
4. 件数制限：論文ごとの送信数の上限
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from gateway.config import GatewayConfig
from gateway.manuscripts import RegisteredManuscript, cosine, normalize
from shared.schemas import BibLookup, OATextFetch, TermLookup

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None


OK = Verdict(True)


def contains_manuscript_text(value: str, manuscript: RegisteredManuscript, min_len: int) -> bool:
    """value に、原稿本文の min_len 文字以上の部分文字列が含まれるか。"""
    text = normalize(value)
    if len(text) < min_len:
        return False
    body = manuscript.body_normalized
    return any(text[i : i + min_len] in body for i in range(len(text) - min_len + 1))


def is_own_title(title: str, own_title: str) -> bool:
    """原稿自身のタイトル（またはその主要な部分）か。未発表のタイトルは、それ自体が秘密情報。"""
    a, b = normalize(title), normalize(own_title)
    if not a or not b:
        return False
    if SequenceMatcher(None, a, b).ratio() >= 0.9:
        return True
    # 副題だけ・接頭辞を除いたものなど、一方が他方に含まれる場合（短すぎる一致は除く）
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 10 and shorter in longer


async def check(
    request: BibLookup | OATextFetch | TermLookup,
    manuscript: RegisteredManuscript | None,
    config: GatewayConfig,
    *,
    sent_count: int,
    embed: Embedder,
) -> Verdict:
    if manuscript is None:
        return Verdict(False, "原稿の照合情報が登録されていません")
    if sent_count >= config.limits.max_requests_per_doc:
        return Verdict(
            False,
            f"この論文の外部要求の上限（{config.limits.max_requests_per_doc} 件）に達しました",
        )
    min_len = config.leak_check.min_substring

    if isinstance(request, BibLookup):
        if request.first_author and contains_manuscript_text(
            request.first_author, manuscript, min_len
        ):
            return Verdict(False, "著者名の欄に原稿本文の文字列が含まれています")
        own_title = manuscript.guard.title
        if request.title and own_title and is_own_title(request.title, own_title):
            return Verdict(False, "原稿自身のタイトルは照会できません")
        if request.doi and contains_manuscript_text(request.doi, manuscript, min_len):
            return Verdict(False, "DOI の欄に原稿本文の文字列が含まれています")
        return OK

    if isinstance(request, OATextFetch):
        if contains_manuscript_text(request.doi, manuscript, min_len):
            return Verdict(False, "DOI の欄に原稿本文の文字列が含まれています")
        return OK

    term = normalize(request.term)
    for coined in manuscript.guard.coined_terms:
        c = normalize(coined)
        if c and (term == c or (len(c) >= 3 and c in term) or (len(term) >= 3 and term in c)):
            return Verdict(False, "原稿で定義された造語・手法名は照会できません")
    if manuscript.claim_vectors:
        [vector] = await embed([request.term])
        similarity = max(cosine(vector, v) for v in manuscript.claim_vectors)
        if similarity > config.leak_check.similarity_threshold:
            return Verdict(
                False, f"原稿の新規性に関わる主張に近い用語です（類似度 {similarity:.2f}）"
            )
    return OK
