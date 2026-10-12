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
}


def text(key: str, lang: Lang, **values: str) -> str:
    return TEXTS[key][lang].format(**values)
