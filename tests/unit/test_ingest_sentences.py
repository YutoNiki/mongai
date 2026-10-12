"""文分割（日本語：句点ベース＋例外処理、英語：pysbd）。"""

import pytest

from core.ingest.sentences import detect_lang, split_en, split_ja, split_sentences


def sents_ja(text: str) -> list[str]:
    return [text[a:b] for a, b in split_ja(text)]


def sents_en(text: str) -> list[str]:
    return [text[a:b] for a, b in split_en(text)]


def test_ja_basic_terminals() -> None:
    assert sents_ja("一文目です。二文目です．三文目！四文目？") == [
        "一文目です。",
        "二文目です．",
        "三文目！",
        "四文目？",
    ]


def test_ja_no_split_inside_brackets() -> None:
    assert sents_ja("彼は「本当か？違うだろう。」と言った。次の文。") == [
        "彼は「本当か？違うだろう。」と言った。",
        "次の文。",
    ]
    assert sents_ja("手法（詳細は後述。図1参照）を用いる．") == [
        "手法（詳細は後述。図1参照）を用いる．"
    ]


def test_ja_decimal_point_is_not_terminal() -> None:
    assert sents_ja("精度は3．5ポイント向上した．次．") == ["精度は3．5ポイント向上した．", "次．"]


def test_ja_ascii_period_is_not_terminal() -> None:
    text = "性能を達成している(Bahdanau et al. 2015; Rush et al. 2015)．次の文．"
    assert sents_ja(text) == [
        "性能を達成している(Bahdanau et al. 2015; Rush et al. 2015)．",
        "次の文．",
    ]


def test_ja_closing_brackets_attach_to_sentence() -> None:
    assert sents_ja("「終わり。」次。") == ["「終わり。」", "次。"]
    assert sents_ja("本当に？！次。") == ["本当に？！", "次。"]


def test_ja_unbalanced_bracket_does_not_stop_splitting() -> None:
    # 箇条書きの "1)" のように閉じない括弧があっても分割が止まらない
    assert sents_ja("1) 一つ目．（注 二つ目．三つ目．") == [
        "1) 一つ目．",
        "（注 二つ目．",
        "三つ目．",
    ]


def test_ja_trailing_text_without_terminal() -> None:
    assert sents_ja("一文目。句点なしの末尾") == ["一文目。", "句点なしの末尾"]


def test_en_abbreviations_and_decimals() -> None:
    text = "We follow Smith et al. (2020). The score was 3.5 points higher, e.g. on CNN/DM. Is it? Yes."
    assert sents_en(text) == [
        "We follow Smith et al. (2020).",
        "The score was 3.5 points higher, e.g. on CNN/DM.",
        "Is it?",
        "Yes.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "First.  Second sentence here.   Third!",
        "Repeated. Repeated. Repeated.",
        "No terminal at the end",
    ],
)
def test_en_offsets_point_into_original(text: str) -> None:
    for start, end in split_en(text):
        assert text[start:end].strip() == text[start:end]
        assert text[start:end]


def test_detect_lang() -> None:
    assert detect_lang("本研究では新しい手法を提案する。") == "ja"
    assert detect_lang("We propose a new method.") == "en"
    assert detect_lang("ExtraPhrase は生成型要約のための手法である") == "ja"
    assert detect_lang("") == "en"


def test_split_sentences_dispatch() -> None:
    assert len(split_sentences("一つ目。二つ目。")) == 2
    assert len(split_sentences("One. Two.", "en")) == 2


def test_ja_sentence_ending_inside_quote() -> None:
    assert sents_ja("「終わり。」次。") == ["「終わり。」", "次。"]
    assert sents_ja("「本当か。」と聞いた。") == ["「本当か。」と聞いた。"]
