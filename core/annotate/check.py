"""指摘の位置（ハイライト）の診断：python -m core.annotate.check <解析ID> [--fix]

論文の本文・引用箇所は表示せず、指摘ごとの判定（一致・合わせ直し・確認できない）とページ番号だけを表示する。
--fix を付けると、合わせ直した位置を保存する（解析済みの結果を直すとき）。
"""

import argparse

import pymupdf

from core.annotate.locate import locate
from core.store import Store
from core.store.files import load_paper


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_id")
    parser.add_argument("--fix", action="store_true", help="合わせ直した位置を保存する")
    parser.add_argument("--db", help="SQLite のパス（既定は /data/db/mongai.sqlite3）")
    args = parser.parse_args()

    store = Store(args.db) if args.db else Store()
    record = store.get_analysis(args.analysis_id)
    if record is None:
        raise SystemExit("解析が見つかりません")
    pdf = load_paper(record.doc_id)
    if pdf is None:
        raise SystemExit("論文のファイルが見つかりません")
    findings = store.get_findings(args.analysis_id)
    counts = {"exact": 0, "relocated": 0, "unverified": 0}
    updated = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        print(f"ページ数 {doc.page_count}、指摘 {len(findings)} 件")
        for f in findings:
            located = locate(doc, f)
            counts[located.status] += 1
            moved = f"（{f.page} → {located.page} ページ）" if located.page != f.page else ""
            print(f"{f.finding_id} {f.category}/{f.subcategory} p.{f.page} {located.status}{moved}")
            updated.append(f.model_copy(update={"bboxes": located.bboxes, "page": located.page}))
    print(
        f"一致 {counts['exact']}、合わせ直し {counts['relocated']}、確認できない {counts['unverified']}"
    )
    if args.fix:
        store.save_findings(args.analysis_id, updated)
        print("位置を保存しました")


if __name__ == "__main__":
    main()
