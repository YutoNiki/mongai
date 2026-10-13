"""再解析の差分とテキストの要約レポート（core/report）。"""

from core.ingest.models import BBox
from core.report.diff import diff_findings, quote_similarity
from tests.unit.test_annotate import finding

BOX = BBox(page=1, x0=72, y0=110, x1=330, y1=123)


def test_quote_similarity_tolerates_small_rewrites() -> None:
    assert (
        quote_similarity("提案手法はすべての課題を解決する。", "提案手法はすべての課題を解決する")
        == 1.0
    )
    assert (
        quote_similarity(
            "提案手法はすべての課題を解決できる。", "提案手法はすべての課題を解決する。"
        )
        > 0.6
    )
    assert quote_similarity("まったく別の文である。", "提案手法はすべての課題を解決する。") < 0.6


def test_diff_matches_by_type_and_quote_not_sentence_id() -> None:
    previous = [
        finding(
            "F-0001", "logic", BOX, "提案手法はすべての課題を解決する。", sentence_ids=["S-00010"]
        ),
        finding("F-0002", "logic", BOX, "この結果は普遍的に成り立つ。", subcategory="leap"),
        finding(
            "F-0003",
            "readability",
            BOX,
            "それが大きい地区",
            subcategory="ambiguous_reference",
            status="dismissed",
            dismiss_reason="意図どおり",
        ),
    ]
    current = [
        # 文の番号が変わっても、同じ種類で文面が近ければ同じ指摘
        finding(
            "F-0001", "logic", BOX, "提案手法はすべての課題を解決できる。", sentence_ids=["S-00012"]
        ),
        # 種類が違えば別の指摘
        finding("F-0002", "logic", BOX, "この結果は普遍的に成り立つ。", subcategory="overclaim"),
    ]
    diff = diff_findings(current, previous)
    assert [(c.finding_id, p.finding_id) for c, p in diff.persisting] == [("F-0001", "F-0001")]
    assert [f.finding_id for f in diff.new] == ["F-0002"]
    # 却下した指摘は「解消」に数えない
    assert [f.finding_id for f in diff.resolved] == ["F-0002"]


def test_diff_pairs_one_to_one() -> None:
    previous = [finding("F-0001", "logic", BOX, "同じ文面の主張である。")]
    current = [
        finding("F-0001", "logic", BOX, "同じ文面の主張である。"),
        finding("F-0002", "logic", BOX, "同じ文面の主張である。"),
    ]
    diff = diff_findings(current, previous)
    assert len(diff.persisting) == 1 and [f.finding_id for f in diff.new] == ["F-0002"]
