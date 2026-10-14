"""評価結果の比較ページ（SPEC 5.4）。eval/results/*.csv から eval/results/index.html を作る。

ダッシュボードと同じ考え方で作る（SPEC 第6章）：
- 色はデジタル庁のパレット（core/annotate/palette.py）から。キーカラーの Blue 1色だけを使う
- 棒の原点は 0、範囲は 0〜1 に固定。値を棒のすぐ横に数字で書く（色だけに頼らない）
- 並び順はラベル（データセットの順）→ モデル（指定した順）。装飾・影・3D は使わない
- 外部のフォント・ライブラリを読み込まない（1つの HTML で完結）

役割・課題・モデルごとに、最新の実行の結果だけを載せる。過去の結果は CSV に残る。
"""

import csv
import html
from pathlib import Path

from core.annotate.palette import (
    BACKGROUND_CONTROL,
    BACKGROUND_STANDARD,
    BLUE_900,
    LABEL_GRAY,
    TEXT_BLACK,
    WHITE,
)

TASK_NAMES = {
    "paragraph": "段落内チェック",
    "flow": "段落間チェック",
    "citation_support": "引用内容の支持判定",
    "second_opinion": "二重チェック（第2のモデル）",
    "rule": "ルールの判定",
}
LABEL_NAMES = {
    "premise_evidence_mismatch": "前提と根拠の不一致",
    "leap": "論理の飛躍",
    "overclaim": "主張の過大",
    "disconnection": "接続の断絶",
    "does_not_support": "引用の不一致",
    "valid_issue": "妥当な指摘を認める",
    "rule_violation": "ルール違反",
    "micro": "全体（micro平均）",
    "control_fp": "対照群の誤検出",
}


def load_results(results_dir: Path) -> list[dict[str, str]]:
    """各 (役割, 課題, モデル) の最新の実行の行だけを返す。"""
    rows: list[dict[str, str]] = []
    for path in sorted(results_dir.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows += list(csv.DictReader(f))
    latest: dict[tuple[str, str, str], str] = {}
    for r in rows:
        key = (r["role"], r["task"], r["model"])
        latest[key] = max(latest.get(key, ""), r["run_at"])
    return [r for r in rows if r["run_at"] == latest[(r["role"], r["task"], r["model"])]]


def _bar(value: str) -> str:
    """0〜1 の値の横棒。値が無い（分母が 0）ときは「-」だけ書く。"""
    if value in ("", "-"):
        return '<span class="na">-</span>'
    width = max(0.0, min(1.0, float(value))) * 100
    return (
        f'<span class="bar" role="img" aria-label="{value}">'
        f'<span class="fill" style="width:{width:.0f}%"></span></span>'
        f'<span class="value">{value}</span>'
    )


def render(rows: list[dict[str, str]]) -> str:
    sections: list[str] = []
    tasks: list[tuple[str, str]] = []
    for r in rows:
        if (r["role"], r["task"]) not in tasks:
            tasks.append((r["role"], r["task"]))
    for role, task in tasks:
        task_rows = [r for r in rows if r["role"] == role and r["task"] == task]
        labels = list(dict.fromkeys(r["label"] for r in task_rows))
        body: list[str] = []
        for label in labels:
            for r in (x for x in task_rows if x["label"] == label):
                name = LABEL_NAMES.get(label, label)
                if label == "control_fp":
                    metrics = (
                        f'<td colspan="3">{html.escape(r["fp"])} / {html.escape(r["n"])} 件で'
                        "指摘あり（少ないほどよい）</td>"
                    )
                else:
                    metrics = "".join(
                        f'<td class="metric">{_bar(r[m])}</td>'
                        for m in ("precision", "recall", "f1")
                    )
                body.append(
                    "<tr>"
                    f'<th scope="row">{html.escape(name)}</th>'
                    f"<td>{html.escape(r['model'])}</td>"
                    f'<td class="num">{html.escape(r["n"])}</td>'
                    f'<td class="num">{html.escape(r["tp"])} / {html.escape(r["fp"])} / {html.escape(r["fn"])}</td>'
                    f"{metrics}"
                    f'<td class="num">{html.escape(r["latency_s"])}</td>'
                    f'<td class="num">{html.escape(r["errors"])}</td>'
                    f"<td>{html.escape(r['run_at'])}</td>"
                    "</tr>"
                )
        title = f"{TASK_NAMES.get(task, task)}の評価（役割 {role}、合成データ）"
        sections.append(
            f"<section><h2>{html.escape(title)}</h2>"
            f'<table><caption class="sr-only">{html.escape(title)}</caption>'
            '<thead><tr><th scope="col">ラベル</th><th scope="col">モデル</th>'
            '<th scope="col">件数</th><th scope="col">TP / FP / FN（件）</th>'
            '<th scope="col">適合率（0〜1）</th><th scope="col">再現率（0〜1）</th>'
            '<th scope="col">F1（0〜1）</th><th scope="col">平均応答時間（秒）</th>'
            '<th scope="col">失敗（件）</th><th scope="col">実行日時</th></tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></section>"
        )
    content = (
        "".join(sections)
        or "<p>評価結果はまだありません。<code>make eval</code> を実行してください。</p>"
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>モデル評価の比較 | SI-NSA</title>
<style>
:root {{ --key: {BLUE_900}; --text: {TEXT_BLACK}; --bg: {BACKGROUND_STANDARD}; --label: {LABEL_GRAY};
  --control: {BACKGROUND_CONTROL}; --surface: {WHITE}; }}
body {{ margin: 0; padding: 24px 16px; background: var(--bg); color: var(--text);
  font-family: "Noto Sans JP", sans-serif; line-height: 1.6; }}
main {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 1.5rem; margin: 0 0 8px; }}
h2 {{ font-size: 1.125rem; margin: 32px 0 8px; }}
p.lead {{ color: var(--label); margin: 0 0 16px; }}
section {{ background: var(--surface); padding: 16px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.875rem; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--control);
  white-space: nowrap; vertical-align: middle; }}
thead th {{ color: var(--label); font-weight: 500; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.metric {{ min-width: 150px; }}
.bar {{ display: inline-block; width: 100px; height: 12px; background: var(--control);
  vertical-align: middle; }}
.fill {{ display: block; height: 100%; background: var(--key); }}
.value {{ margin-left: 6px; font-variant-numeric: tabular-nums; }}
.na {{ color: var(--label); }}
.sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }}
</style></head>
<body><main>
<h1>モデル評価の比較</h1>
<p class="lead">役割・課題・モデルごとに最新の実行の結果を示す。棒は 0〜1 の範囲（原点 0）。
数値の元データは同じフォルダの CSV にある。データはすべて合成データ。</p>
{content}
</main></body></html>
"""


def write_report(results_dir: Path) -> Path:
    path = results_dir / "index.html"
    path.write_text(render(load_results(results_dir)), encoding="utf-8")
    return path


if __name__ == "__main__":
    from eval.run import RESULTS_DIR

    print(write_report(RESULTS_DIR))
