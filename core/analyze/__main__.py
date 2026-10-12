"""論理・読みやすさの解析の確認用 CLI。

python -m core.analyze <PDF> [--max-paragraphs N] [--output-lang paper|ja] [--db PATH] [--json]

公開論文・合成データでの動作確認用。実際の未発表論文は、Claude Code 等を使わずに扱うこと。
"""

import argparse
import asyncio
from collections import Counter

from core.analyze.pipeline import AnalysisSettings, analyze_logic
from core.ingest.pipeline import ingest_pdf
from core.llm.client import OitomaClient
from core.logging_config import configure_logging
from core.store import Store


async def _run(args: argparse.Namespace) -> None:
    settings = AnalysisSettings(
        output_lang=args.output_lang,
        max_paragraphs=args.max_paragraphs,
        with_flow_check=not args.no_flow,
        with_suggestions=not args.no_suggestions,
    )
    async with OitomaClient() as client:
        doc = await ingest_pdf(args.pdf, use_grobid=not args.no_grobid, llm=client)
        store = Store(args.db) if args.db else None
        analysis_id = None
        if store is not None:
            store.save_document(doc)
            analysis_id = store.create_analysis(doc.doc_id, kind="logic", settings=settings)

        def progress(stage: str, done: int, total: int) -> None:
            if done == total or done % 10 == 0:
                print(f"[{stage}] {done}/{total}", flush=True)

        try:
            result = await analyze_logic(doc, client, settings, on_progress=progress)
        except Exception as exc:
            if store is not None and analysis_id is not None:
                store.finish_analysis(
                    analysis_id, status="failed", error=str(exc), api_usage=client.usage.rows()
                )
            raise
        if store is not None and analysis_id is not None:
            store.save_structures(analysis_id, result.structures)
            store.save_findings(analysis_id, result.findings)
            store.finish_analysis(
                analysis_id,
                status="completed",
                warnings=doc.warnings + result.warnings,
                api_usage=result.api_usage,
            )

    if args.json:
        print(result.model_dump_json(indent=2))
        return
    print(f"\n対象：{doc.title}（{doc.lang}）")
    if analysis_id:
        print(f"解析ID：{analysis_id}（{args.db}）")
    counts = Counter((f.severity, f.subcategory) for f in result.findings)
    print(f"指摘：{len(result.findings)} 件 {dict(counts)}")
    for f in result.findings:
        print(
            f"\n{f.finding_id} [{f.category}/{f.subcategory}・{f.severity}・{f.votes}/{f.samples}] p.{f.page}"
        )
        print(f"  箇所：{f.quote[:120]}")
        print(f"  指摘：{f.issue}")
        print(f"  理由：{f.rationale}")
        for s in f.suggestions:
            print(f"  改善案：{s}")
    print("\nおいとまAPIの利用回数：")
    for row in result.api_usage:
        print(
            f"  {row.role:24} {row.model:22} requests={row.requests} retries={row.retries} "
            f"failures={row.failures} tokens={row.prompt_tokens}+{row.completion_tokens}"
        )
    for w in result.warnings:
        print(f"警告：{w}")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--no-grobid", action="store_true")
    parser.add_argument("--max-paragraphs", type=int)
    parser.add_argument("--output-lang", choices=["paper", "ja"], default="paper")
    parser.add_argument("--no-flow", action="store_true", help="段落間のチェックを省く")
    parser.add_argument("--no-suggestions", action="store_true", help="改善案を省く")
    parser.add_argument("--db", help="結果を保存する SQLite のパス")
    parser.add_argument("--json", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
