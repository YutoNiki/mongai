"""モデル評価ハーネス（SPEC 5.4）。

python -m eval.run --role logic_check --models qwen3.8-27b-it,Ternary-Bonsai-2-27B
    [--lang ja|en] [--limit N] [--samples N]

役割の設定（config/models.yaml）のモデルだけを差し替えて、解析と同じ処理（プロンプト・多数決・
後処理）で合成データを判定し、ラベルごとの適合率・再現率・F1・平均応答時間を表で出す。
結果は eval/results/ に CSV（指標）と JSONL（1件ごとの予測）で保存し、比較ページ
eval/results/index.html を作り直す。

対応する役割：
- logic_check：段落内チェック（paragraph.jsonl）と段落間チェック（flow.jsonl）
- citation_support：引用内容の支持判定（citation_support.jsonl）

使うのは合成データだけ。おいとまAPI 以外には送らない。
"""

import argparse
import asyncio
import csv
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.analyze.citation import ROLE_SUPPORT, SupportJudgement, support_messages
from core.analyze.context import output_language
from core.analyze.flow_check import check_flow
from core.analyze.paragraph_check import check_paragraph
from core.analyze.structure import extract_structure
from core.llm.client import OitomaClient, OitomaError
from core.llm.config import ModelsConfig, get_models_config
from core.logging_config import configure_logging
from eval.dataset import (
    DATASET_DIR,
    CitationItem,
    FlowItem,
    ParagraphItem,
    build_targets,
    load_jsonl,
    paragraph_id,
    sentence_id,
)
from eval.metrics import NONE_LABEL, Outcome, Score, mean_latency, score
from eval.report import write_report

RESULTS_DIR = Path(__file__).resolve().parent / "results"
JST = ZoneInfo("Asia/Tokyo")

# 段落内チェックで評価するラベル（データセットで仕込んだ種類）
PARAGRAPH_LABELS = ("premise_evidence_mismatch", "leap", "overclaim")
FLOW_LABELS = ("disconnection",)
CITATION_LABELS = ("does_not_support",)

Task = Callable[[OitomaClient, str, int | None], Awaitable[list[Outcome]]]


def with_model(config: ModelsConfig, role: str, model: str, samples: int | None) -> ModelsConfig:
    """役割のモデル（と多数決の回数）だけを差し替えた設定を返す。"""
    update: dict[str, object] = {"model": model}
    if samples is not None:
        update["samples"] = samples
    roles = dict(config.roles)
    roles[role] = config.role(role).model_copy(update=update)
    return config.model_copy(update={"roles": roles})


async def _timed[T](coro: Awaitable[T]) -> tuple[T | None, float, str | None]:
    started = time.monotonic()
    try:
        return await coro, time.monotonic() - started, None
    except OitomaError as exc:
        return None, time.monotonic() - started, type(exc).__name__


# ---------------------------------------------------------------- 段落内チェック


async def run_paragraph(client: OitomaClient, lang: str, limit: int | None) -> list[Outcome]:
    items = _select(load_jsonl(DATASET_DIR / "paragraph.jsonl", ParagraphItem), lang, limit)

    async def judge(item: ParagraphItem) -> Outcome:
        (target,) = build_targets(
            item.id, item.lang, item.section_title, item.section_role, [item.sentences]
        )
        out_lang = output_language(item.lang, item.lang, "paper")

        async def once() -> set[str]:
            structure = await extract_structure(
                client, target, prompt_lang=item.lang, output_language=out_lang
            )
            voted, _ = await check_paragraph(
                client,
                target,
                structure,
                prompt_lang=item.lang,
                output_language=out_lang,
                known_sentences={s.sentence_id for s in target.sentences},
                context_text="",
            )
            wanted = {sentence_id(item.id, 1, n) for n in item.target}
            hits = {
                v.item.type
                for v in voted
                if v.item.type == item.label and wanted & set(v.item.sentence_ids)
            }
            return {v.item.type for v in voted if v.item.type in PARAGRAPH_LABELS} | {
                f"hit:{t}" for t in hits
            }

        found, latency, error = await _timed(once())
        return _outcome(item.id, item.label, found or set(), latency, error)

    return list(await asyncio.gather(*(judge(i) for i in items)))


# ---------------------------------------------------------------- 段落間チェック


async def run_flow(client: OitomaClient, lang: str, limit: int | None) -> list[Outcome]:
    items = _select(load_jsonl(DATASET_DIR / "flow.jsonl", FlowItem), lang, limit)

    async def judge(item: FlowItem) -> Outcome:
        targets = build_targets(
            item.id, item.lang, item.section_title, item.section_role, item.paragraphs
        )
        out_lang = output_language(item.lang, item.lang, "paper")

        async def once() -> set[str]:
            structures = await asyncio.gather(
                *(
                    extract_structure(client, t, prompt_lang=item.lang, output_language=out_lang)
                    for t in targets
                )
            )
            by_paragraph = {s.paragraph_id: s for s in structures}
            voted, _, _ = await check_flow(
                client, targets, by_paragraph, prompt_lang=item.lang, output_language=out_lang
            )
            found: set[str] = {v.item.type for v in voted if v.item.type in FLOW_LABELS}
            if item.target is not None and any(
                v.item.type == item.label
                and v.item.paragraph_id == paragraph_id(item.id, item.target)
                for v in voted
            ):
                found.add(f"hit:{item.label}")
            return found

        found, latency, error = await _timed(once())
        return _outcome(item.id, item.label, found or set(), latency, error)

    return list(await asyncio.gather(*(judge(i) for i in items)))


# ---------------------------------------------------------------- 引用内容の支持判定


async def run_citation(client: OitomaClient, lang: str, limit: int | None) -> list[Outcome]:
    items = _select(load_jsonl(DATASET_DIR / "citation_support.jsonl", CitationItem), lang, limit)

    async def judge(item: CitationItem) -> Outcome:
        messages = support_messages(
            sentence=item.sentence,
            context=item.context,
            reference=item.reference,
            abstract=item.abstract,
            source="eval",
            prompt_lang=item.lang,
            output_language=output_language(item.lang, item.lang, "paper"),
        )
        result, latency, error = await _timed(
            client.chat_json(ROLE_SUPPORT, messages, SupportJudgement, lang=item.lang)
        )
        found: set[str] = set()
        if result is not None and result.verdict == "does_not_support":
            found = {"does_not_support", "hit:does_not_support"}
        # 支持している引用（対照群）は、正解ラベルを "none" として数える
        gold = item.label if item.label == "does_not_support" else NONE_LABEL
        return _outcome(item.id, gold, found, latency, error)

    return list(await asyncio.gather(*(judge(i) for i in items)))


# ---------------------------------------------------------------- 共通


def _select[T: ParagraphItem | FlowItem | CitationItem](
    items: list[T], lang: str, limit: int | None
) -> list[T]:
    chosen = [i for i in items if lang in ("all", i.lang)]
    return chosen[:limit] if limit else chosen


def _outcome(
    item_id: str, gold: str, found: set[str], latency: float, error: str | None
) -> Outcome:
    labels = frozenset(f for f in found if not f.startswith("hit:"))
    hit = gold != NONE_LABEL and f"hit:{gold}" in found
    return Outcome(item_id, gold, hit, labels, latency, error)


TASKS: dict[str, list[tuple[str, Task, tuple[str, ...]]]] = {
    "logic_check": [
        ("paragraph", run_paragraph, PARAGRAPH_LABELS),
        ("flow", run_flow, FLOW_LABELS),
    ],
    ROLE_SUPPORT: [("citation_support", run_citation, CITATION_LABELS)],
}


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def print_table(rows: list[dict[str, object]]) -> None:
    header = [
        "task",
        "model",
        "label",
        "n",
        "tp",
        "fp",
        "fn",
        "precision",
        "recall",
        "f1",
        "latency_s",
        "errors",
    ]
    widths = [max(len(str(h)), *(len(str(r[h])) for r in rows)) for h in header]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)))
    for r in rows:
        print("  ".join(str(r[h]).ljust(w) for h, w in zip(header, widths, strict=True)))


def score_rows(
    role: str, task: str, model: str, outcomes: list[Outcome], labels: Sequence[str], run_at: str
) -> list[dict[str, object]]:
    errors = sum(o.error is not None for o in outcomes)
    latency = round(mean_latency(outcomes), 1)
    rows: list[dict[str, object]] = []
    for s in score(outcomes, labels):
        rows.append(_row(role, task, model, s, latency, errors, run_at))
    return rows


def _row(
    role: str, task: str, model: str, s: Score, latency: float, errors: int, run_at: str
) -> dict[str, object]:
    return {
        "run_at": run_at,
        "role": role,
        "task": task,
        "model": model,
        "label": s.label,
        "n": s.n,
        "tp": s.tp,
        "fp": s.fp,
        "fn": s.fn,
        "precision": _fmt(s.precision),
        "recall": _fmt(s.recall),
        "f1": _fmt(s.f1),
        "latency_s": latency,
        "errors": errors,
    }


async def main(args: argparse.Namespace) -> None:
    if args.role not in TASKS:
        raise SystemExit(f"役割 {args.role} の評価には未対応です（対応：{', '.join(TASKS)}）")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    base = get_models_config()
    now = datetime.now(JST)
    run_at = now.isoformat(timespec="seconds")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(exist_ok=True)
    rows: list[dict[str, object]] = []
    predictions = RESULTS_DIR / f"{stamp}_{args.role}_predictions.jsonl"
    with predictions.open("w", encoding="utf-8") as pred_file:
        for model in models:
            config = with_model(base, args.role, model, args.samples)
            async with OitomaClient(config) as client:
                for task, run, labels in TASKS[args.role]:
                    print(f"[{model}] {task} を評価しています…", flush=True)
                    outcomes = await run(client, args.lang, args.limit)
                    rows += score_rows(args.role, task, model, outcomes, labels, run_at)
                    for o in outcomes:
                        record = asdict(o) | {"model": model, "task": task}
                        record["predicted"] = sorted(o.predicted)
                        pred_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    path = RESULTS_DIR / f"{stamp}_{args.role}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print()
    print_table(rows)
    report = write_report(RESULTS_DIR)
    print(f"\n結果：{path.name}（1件ごとの予測：{predictions.name}）")
    print(f"比較ページ：{report.name}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="役割×モデルの評価（合成データ）")
    parser.add_argument("--role", required=True)
    parser.add_argument("--models", required=True, help="カンマ区切りのモデルID")
    parser.add_argument("--lang", choices=["all", "ja", "en"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="課題ごとに先頭 N 件だけ使う")
    parser.add_argument(
        "--samples", type=int, default=None, help="多数決の回数（既定は config/models.yaml の値）"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main(parse_args()))
