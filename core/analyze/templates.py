"""LLM を使わずに作る指摘の文面（書誌の照合結果など）。指摘の言語に合わせて日英を用意する。"""

from core.ingest.models import Lang

TEXTS: dict[str, dict[Lang, str]] = {
    "bib_not_found.issue": {
        "ja": "参考文献の DOI（{doi}）が、書誌データベースで見つかりませんでした。",
        "en": "The DOI of this reference ({doi}) was not found in the bibliographic databases.",
    },
    "bib_not_found.rationale": {
        "ja": "DOI の誤記の可能性があります。読者や査読者が文献をたどれなくなります。",
        "en": "The DOI may be mistyped, which prevents readers and reviewers from locating the reference.",
    },
    "bib_mismatch.issue": {
        "ja": "参考文献の書誌情報が、書誌データベースの記録と一致しません（{fields}）。",
        "en": "The bibliographic details of this reference do not match the database record ({fields}).",
    },
    "bib_mismatch.rationale": {
        "ja": "{details}。書誌の誤りは、査読で信頼性を損なう原因になります。",
        "en": "{details}. Bibliographic errors undermine credibility in review.",
    },
    "field.year": {"ja": "年", "en": "year"},
    "field.first_author": {"ja": "第一著者", "en": "first author"},
    "field.title": {"ja": "タイトル", "en": "title"},
    "field.volume": {"ja": "巻", "en": "volume"},
    "field.pages": {"ja": "ページ", "en": "pages"},
    "detail": {
        "ja": "{field}：原稿「{ours}」、データベース「{theirs}」",
        "en": '{field}: manuscript "{ours}", database "{theirs}"',
    },
    "citation_unsupported.issue": {
        "ja": "引用している文の主張が、引用先の文献（{reference}）のアブストラクトと対応していません。",
        "en": "The claim of this sentence does not correspond to the abstract of the cited reference ({reference}).",
    },
    # 研究室知識（core/lab/check.py）
    "lab.rationale": {
        "ja": "研究室の過去の査読・添削で、同じ種類の問題が指摘されています：「{comment}」（{source}）",
        "en": 'The same kind of problem was pointed out in the lab\'s past reviews or edits: "{comment}" ({source})',
    },
    # 機械判定のルール（core/rules/mechanical.py）
    "abbr_undefined.issue": {
        "ja": "略語「{acronym}」が、正式名称とともに定義されていません。",
        "en": 'The abbreviation "{acronym}" is not defined with its full name.',
    },
    "abbr_undefined.rationale": {
        "ja": (
            "分野外の読者や査読者が略語の意味を取れない可能性があります。"
            "初出時に「正式名称（略語）」の形で定義してください。"
        ),
        "en": (
            "Readers and reviewers outside the field may not know what it stands for. "
            "Define it at first use, as in 'full name (ABBR)'."
        ),
    },
    "abbr_late.issue": {
        "ja": "略語「{acronym}」が、定義より前に使われています。",
        "en": 'The abbreviation "{acronym}" is used before it is defined.',
    },
    "abbr_late.rationale": {
        "ja": "略語は初出時に定義するのが一般的です。定義を最初に使う箇所に移してください。",
        "en": "Abbreviations are usually defined at first use. Move the definition to the first occurrence.",
    },
    "fig_unreferenced.issue": {
        "ja": "{label}が、本文中で参照されていません。",
        "en": "{label} is not referred to in the text.",
    },
    "fig_unreferenced.rationale": {
        "ja": "参照されない図表は、何を示すためのものかが読者に伝わりません。本文で図表に言及してください。",
        "en": "Readers cannot tell what an unreferenced figure or table is meant to show. Refer to it in the text.",
    },
    "fig_missing.issue": {
        "ja": "本文が{label}を参照していますが、その図表が見つかりません。",
        "en": "The text refers to {label}, but no such figure or table was found.",
    },
    "fig_missing.rationale": {
        "ja": "図表の番号の誤り、または図表の入れ忘れの可能性があります。",
        "en": "The number may be wrong, or the figure or table may be missing.",
    },
    "unit.issue": {
        "ja": "単位の表記「{variant}」が、論文の中のほかの箇所の表記「{main}」とそろっていません。",
        "en": 'The unit notation "{variant}" differs from "{main}" used elsewhere in the paper.',
    },
    "unit.rationale": {
        "ja": "同じ単位は論文全体で同じ表記にしてください（「{main}」は {count} か所で使われています）。",
        "en": 'Use the same notation for a unit throughout the paper ("{main}" is used in {count} places).',
    },
}


def text(key: str, lang: Lang, **values: str) -> str:
    return TEXTS[key][lang].format(**values)
