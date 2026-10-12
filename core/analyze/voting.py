"""自己一貫性（同じ判定を複数回行い、多数決を取る）の集約。

LLM の指摘は自由文なので、完全一致では比べられない。種類が同じで、対象（文・段落）が重なる指摘を
同じ指摘とみなして束ね、判定回数の過半数に現れた束だけを残す。
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Voted[T]:
    item: T  # 代表（最初に現れたもの）
    votes: int  # この指摘が現れた判定の回数
    members: list[T]


def majority[T](samples: list[list[T]], *, same: Callable[[T, T], bool], n: int) -> list[Voted[T]]:
    """samples（判定ごとの指摘のリスト）から、n 回中の過半数に現れた指摘を返す。

    失敗した判定は samples に含めないが、n には数える（失敗が多いほど残りにくくする）。
    1回の判定の中の指摘どうしは、同じ束にまとめない。
    """
    clusters: list[list[tuple[int, T]]] = []
    for index, items in enumerate(samples):
        for item in items:
            for cluster in clusters:
                if all(i != index for i, _ in cluster) and any(same(item, m) for _, m in cluster):
                    cluster.append((index, item))
                    break
            else:
                clusters.append([(index, item)])
    needed = n // 2 + 1
    return [
        Voted(item=cluster[0][1], votes=len(cluster), members=[m for _, m in cluster])
        for cluster in clusters
        if len(cluster) >= needed
    ]


def most_common[T](values: list[T]) -> T:
    """最頻値。同数の場合は先に現れたもの。"""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)
