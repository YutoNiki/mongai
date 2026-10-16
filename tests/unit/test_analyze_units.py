"""解析の部品（多数決・対象の選択・出力の検証・改善案の検査）。"""

import itertools

from core.analyze.context import output_language, select_targets
from core.analyze.flow_check import chunk_lines, clean_flow_issues
from core.analyze.models import FlowIssue, ParagraphIssue
from core.analyze.paragraph_check import clean_issues
from core.analyze.suggest import keeps_terms, protected_terms
from core.analyze.voting import majority, most_common, vote
from tests.unit.doc_factory import make_doc

LONG = "本研究では論文の読みにくさを検出する新しい手法を提案し，その有効性を二つのデータセットで評価する．"


def test_majority_keeps_issues_seen_in_most_samples() -> None:
    same = lambda a, b: a[0] == b[0] and bool(set(a[1]) & set(b[1]))  # noqa: E731
    samples = [
        [("leap", {"S-1"}), ("overclaim", {"S-2"})],
        [("leap", {"S-1", "S-3"})],
        [("overclaim", {"S-9"})],
    ]
    voted = majority(samples, same=same, n=3)
    assert [(v.item[0], v.votes) for v in voted] == [("leap", 2)]


def test_majority_does_not_merge_within_one_sample() -> None:
    same = lambda a, b: a == b  # noqa: E731
    voted = majority([["x", "x"], []], same=same, n=2)
    assert voted == []  # 1回の判定で2つ出ても、2回中1回の判定にしか現れていない


def test_majority_counts_failed_samples_against() -> None:
    same = lambda a, b: a == b  # noqa: E731
    assert majority([["x"]], same=same, n=3) == []
    assert [v.votes for v in majority([["x"]], same=same, n=1)] == [1]


def _judge(answers: list[list[str] | Exception]) -> tuple[list[int], object]:
    calls: list[int] = []
    queue = iter(answers)

    async def judge() -> list[str]:
        calls.append(1)
        answer = next(queue)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return calls, judge


async def test_vote_stops_early_when_first_samples_agree() -> None:
    same = lambda a, b: a == b  # noqa: E731
    calls, judge = _judge([["x"], ["x"], ["y"]])
    ballot = await vote(judge, same=same, n=3, early_stop=True)  # type: ignore[arg-type]
    assert len(calls) == 2 and ballot.attempts == 2
    assert [(v.item, v.votes, v.samples) for v in ballot.voted] == [("x", 2, 2)]


async def test_vote_continues_when_undecided() -> None:
    same = lambda a, b: a == b  # noqa: E731
    calls, judge = _judge([["x"], ["y"], ["x"]])
    ballot = await vote(judge, same=same, n=3, early_stop=True)  # type: ignore[arg-type]
    assert len(calls) == 3
    assert [(v.item, v.votes, v.samples) for v in ballot.voted] == [("x", 2, 3)]
    # 1回失敗したら、もう1回判定する
    calls, judge = _judge([[], RuntimeError("x"), []])
    ballot = await vote(judge, same=same, n=3, early_stop=True)  # type: ignore[arg-type]
    assert len(calls) == 2 and ballot.voted == [] and not ballot.all_failed
    calls, judge = _judge([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
    ballot = await vote(judge, same=same, n=3, early_stop=True)  # type: ignore[arg-type]
    assert len(calls) == 3 and ballot.all_failed


async def test_vote_without_early_stop_runs_all() -> None:
    same = lambda a, b: a == b  # noqa: E731
    calls, judge = _judge([["x"], ["x"], ["y"]])
    ballot = await vote(judge, same=same, n=3)  # type: ignore[arg-type]
    assert len(calls) == 3 and [(v.votes, v.samples) for v in ballot.voted] == [(2, 3)]


def test_most_common_prefers_first_on_tie() -> None:
    assert most_common(["major", "critical", "critical", "major"]) == "major"


def test_output_language() -> None:
    assert output_language("ja", "ja", "paper") == "日本語"
    assert output_language("en", "en", "paper") == "English"
    assert output_language("en", "en", "ja") == "Japanese"


def test_select_targets_skips_non_body_text() -> None:
    doc = make_doc(
        [
            (
                "1 はじめに",
                "introduction",
                [[LONG, LONG], ["短い．"], ["著者名 山田太郎 鈴木花子 東京大学 所属なし"]],
            ),
            ("参考文献", "references", [[LONG]]),
            ("2 手法", "methods", [[LONG]]),
        ],
        front=[["キーワード：要約，言い換え，データ拡張．"], [LONG * 4]],
    )
    targets = select_targets(doc)
    texts = [t.paragraph.paragraph_id for t in targets]
    # 冒頭の短い段落・文末の無い段落・短すぎる段落・参考文献は対象外
    assert texts == ["P-0002", "P-0003", "P-0007"]
    assert targets[0].previous is not None and targets[0].previous.paragraph_id == "P-0001"
    assert targets[1].previous is not None and targets[1].previous.paragraph_id == "P-0002"
    assert select_targets(doc, max_paragraphs=1)[0].paragraph.paragraph_id == "P-0002"


def test_clean_issues_validates_ids_and_quotes() -> None:
    doc = make_doc(
        [("1 はじめに", "introduction", [[LONG, "したがって本手法は全てのタスクで有効である．"]])]
    )
    target = select_targets(doc)[0]
    known = {s.sentence_id for s in doc.sentences}
    issues = [
        ParagraphIssue(
            type="overclaim",
            sentence_ids=["S-00002", "S-99999"],
            quote="全てのタスクで有効",
            issue="i",
            rationale="r",
            severity="major",
            related_sentence_ids=["S-00001", "S-77777"],
        ),
        ParagraphIssue(
            type="leap",
            sentence_ids=["S-00002"],
            quote="原文に無い言い換え",
            issue="i",
            rationale="r",
            severity="suggestion",
        ),
        ParagraphIssue(
            type="leap",
            sentence_ids=["S-55555"],
            quote="x",
            issue="i",
            rationale="r",
            severity="major",
        ),
    ]
    cleaned = clean_issues(issues, target, known)
    assert [(c.sentence_ids, c.quote, c.related_sentence_ids) for c in cleaned] == [
        (["S-00002"], "全てのタスクで有効", ["S-00001"]),
    ]


def test_clean_flow_issues() -> None:
    doc = make_doc(
        [
            ("1 はじめに", "introduction", [[LONG], [LONG]]),
            ("2 手法", "methods", [[LONG]]),
        ]
    )
    targets = select_targets(doc)
    issues = [
        FlowIssue(
            type="disconnection", paragraph_id="P-0002", issue="i", rationale="r", severity="major"
        ),
        # 新しいセクションの最初の段落での断絶は除く
        FlowIssue(
            type="disconnection", paragraph_id="P-0003", issue="i", rationale="r", severity="major"
        ),
        FlowIssue(
            type="section_role_violation",
            paragraph_id="P-0003",
            previous_paragraph_id="P-0001",
            issue="i",
            rationale="r",
            severity="suggestion",
        ),
        FlowIssue(
            type="disconnection", paragraph_id="P-9999", issue="i", rationale="r", severity="major"
        ),
    ]
    cleaned = clean_flow_issues(issues, targets, [0, 1, 2])
    assert [(c.type, c.paragraph_id, c.previous_paragraph_id) for c in cleaned] == [
        ("disconnection", "P-0002", "P-0001"),
        ("section_role_violation", "P-0003", None),
    ]


def test_chunk_lines_overlap() -> None:
    lines = ["x" * 300 for _ in range(10)]  # 1行あたり約100トークン
    chunks = chunk_lines(lines, max_tokens=350)
    assert chunks[0][0] == 0 and chunks[-1][-1] == 9
    for a, b in itertools.pairwise(chunks):
        assert b[0] == a[-1]  # 1行ずつ重ねる


def test_chunk_lines_max_lines() -> None:
    chunks = chunk_lines(["短い行"] * 40, max_tokens=10_000, max_lines=16)
    assert all(len(c) <= 16 for c in chunks) and len(chunks) == 3
    assert chunks[0][0] == 0 and chunks[-1][-1] == 39
    for a, b in itertools.pairwise(chunks):
        assert b[0] == a[-1]


def test_protected_terms() -> None:
    text = "BERT と ROUGE-1 で 3.5 ポイント向上した [12]（田中, 2020）(Smith et al., 2019)．"
    assert set(protected_terms(text)) >= {
        "BERT",
        "ROUGE-1",
        "3.5",
        "[12]",
        "（田中, 2020）",
        "(Smith et al., 2019)",
    }
    assert keeps_terms(
        text,
        "BERT と ROUGE-1 では [12]（田中, 2020）(Smith et al., 2019) と比べ 3.5 ポイント向上した．",
    )
    assert not keeps_terms(text, "事前学習モデルで 3.5 ポイント向上した．")


def test_paragraph_is_given_as_one_continuous_text() -> None:
    from core.analyze.context import numbered_paragraph

    doc = make_doc([("1 はじめに", "introduction", [[LONG, "次の文です．"]])])
    text = numbered_paragraph(doc.sentences)
    assert text == f"【S-00001】{LONG}【S-00002】次の文です．"
    assert "\n" not in text


def test_targets_know_next_paragraph() -> None:
    doc = make_doc([("1 はじめに", "introduction", [[LONG], [LONG], [LONG]])])
    targets = select_targets(doc)
    assert [t.next.paragraph_id if t.next else None for t in targets] == ["P-0002", "P-0003", None]


def test_truncation_issues_are_dropped() -> None:
    doc = make_doc(
        [
            (
                "1 はじめに",
                "introduction",
                [
                    [
                        LONG,
                        "抽出された事例は，真の訓練データとし",
                        "完結した文が主語と述語で対応しない．",
                    ]
                ],
            )
        ]
    )
    target = select_targets(doc)[0]
    known = {s.sentence_id for s in doc.sentences}

    def issue(t: str, sid: str, truncation: bool = False) -> ParagraphIssue:
        return ParagraphIssue(
            type=t,
            sentence_ids=[sid],
            quote=next(s.text for s in doc.sentences if s.sentence_id == sid),
            issue="i",
            rationale="r",
            severity="major",
            about_truncation=truncation,
        )

    cleaned = clean_issues(
        [
            issue(
                "leap", "S-00001", truncation=True
            ),  # モデルが「途切れ」を理由にしたと申告 → 捨てる
            issue("subject_predicate_mismatch", "S-00002"),  # 文末記号の無い文へのねじれ → 捨てる
            issue("subject_predicate_mismatch", "S-00003"),  # 完結した文へのねじれ → 残す
            issue("leap", "S-00002"),  # ねじれ以外は残す
        ],
        target,
        known,
    )
    assert [(c.type, c.sentence_ids) for c in cleaned] == [
        ("subject_predicate_mismatch", ["S-00003"]),
        ("leap", ["S-00002"]),
    ]


def test_is_complete() -> None:
    from core.analyze.paragraph_check import is_complete

    assert is_complete("文です．") and is_complete("文です。」") and is_complete("It works.")
    assert is_complete("（後述．）") and is_complete('He said "yes."')
    assert not is_complete("真の訓練データとし") and not is_complete("generated by the")
