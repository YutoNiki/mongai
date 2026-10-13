"""評価の指標（適合率・再現率・F1）。

1件のデータ（段落・段落の並び・引用）ごとに、仕込んだ問題の種類を正解ラベル、モデルが出した指摘の
種類の集合を予測として数える。

- 真陽性（TP）：正解ラベルが L のデータで、L の指摘が問題の箇所に出た
- 偽陰性（FN）：正解ラベルが L のデータで、L の指摘が問題の箇所に出なかった
- 偽陽性（FP）：正解ラベルが L 以外のデータ（対照群を含む）で、L の指摘が出た

失敗した判定（JSON が得られない等）は、予測なしとして数える。
"""

from collections.abc import Iterable
from dataclasses import dataclass

NONE_LABEL = "none"


@dataclass(frozen=True)
class Outcome:
    """1件のデータの判定結果。"""

    item_id: str
    gold: str  # 正解ラベル（対照群は "none"）
    hit: bool  # 正解ラベルの指摘が問題の箇所に出たか（対照群は常に False）
    predicted: frozenset[str]  # 出た指摘の種類（評価の対象のラベルだけ）
    latency_s: float
    error: str | None = None


@dataclass(frozen=True)
class Score:
    label: str  # ラベル。全体は "micro"、対照群の誤検出は "control_fp"
    n: int  # 正解ラベルが label のデータの件数
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


def score(outcomes: Iterable[Outcome], labels: Iterable[str]) -> list[Score]:
    """ラベルごとの指標と、全ラベルを合わせた micro 平均、対照群の誤検出件数を返す。"""
    results = list(outcomes)
    scores: list[Score] = []
    for label in labels:
        if label == NONE_LABEL:
            continue
        gold = [o for o in results if o.gold == label]
        tp = sum(o.hit for o in gold)
        fp = sum(label in o.predicted for o in results if o.gold != label)
        scores.append(Score(label=label, n=len(gold), tp=tp, fp=fp, fn=len(gold) - tp))
    scores.append(
        Score(
            label="micro",
            n=sum(s.n for s in scores),
            tp=sum(s.tp for s in scores),
            fp=sum(s.fp for s in scores),
            fn=sum(s.fn for s in scores),
        )
    )
    control = [o for o in results if o.gold == NONE_LABEL]
    if control:
        flagged = sum(bool(o.predicted) for o in control)
        # 対照群：何か指摘が出たら誤検出（fp）、出なければ正しく見逃した（tp に数えない）
        scores.append(Score(label="control_fp", n=len(control), tp=0, fp=flagged, fn=0))
    return scores


def mean_latency(outcomes: Iterable[Outcome]) -> float:
    values = [o.latency_s for o in outcomes]
    return sum(values) / len(values) if values else 0.0
