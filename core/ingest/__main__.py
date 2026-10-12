"""PDF構造化の確認用 CLI：python -m core.ingest <PDF> [--no-grobid] [--llm] [--json]

公開論文・合成データでの動作確認用。実際の未発表論文は、Claude Code 等を使わずに扱うこと。
"""

import argparse
import asyncio
from collections import Counter

from core.ingest.pipeline import ingest_pdf
from core.llm.client import OitomaClient
from core.logging_config import configure_logging


async def _run(args: argparse.Namespace) -> None:
    llm = OitomaClient() if args.llm else None
    try:
        doc = await ingest_pdf(args.pdf, use_grobid=not args.no_grobid, llm=llm)
    finally:
        if llm is not None:
            await llm.aclose()
    if args.json:
        print(doc.model_dump_json(indent=2))
        return
    print(f"タイトル：{doc.title}")
    print(f"言語：{doc.lang} / ページ数：{doc.page_count}")
    print(f"段落：{len(doc.paragraphs)} {dict(Counter(p.kind for p in doc.paragraphs))}")
    print(f"文：{len(doc.sentences)} / 参考文献：{len(doc.references)}")
    resolved = sum(c.resolved for c in doc.citations)
    print(f"引用：{len(doc.citations)}（対応付け済み {resolved}）")
    print("セクション：")
    for s in doc.sections:
        print(
            f"  {s.section_id} [{s.role}] {s.number or ''} {s.title}（段落 {len(s.paragraph_ids)}）"
        )
    for w in doc.warnings:
        print(f"警告：{w}")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--no-grobid", action="store_true")
    parser.add_argument("--llm", action="store_true", help="参考文献の構造化に高速モデルを使う")
    parser.add_argument("--json", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
