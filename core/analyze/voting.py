"""自己一貫性（同じ判定を複数回行い、多数決を取る）の集約。

LLM の指摘は自由文なので、完全一致では比べられない。種類が同じで、対象（文・段落）が重なる指摘を
同じ指摘とみなして束ね、判定回数の過半数に現れた束だけを残す。
早期終了（early_stop）：まず過半数に必要な回数だけ判定し、残りの判定で結果が変わりうるときだけ
1回ずつ追加する。3回中2回で決める設定なら、最初の2回が一致すれば3回目を送らない。
"""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Voted[T]:
    item: T  # 代表（最初に現れたもの）
    votes: int  # この指摘が現れた判定の回数
    members: list[T]
    samples: int  # 実際に行った判定の回数（失敗を含む）


def majority[T](
    samples: list[list[T]],
    *,
    same: Callable[[T, T], bool],
    n: int,
    attempts: int | None = None,
) -> list[Voted[T]]:
    """samples（判定ごとの指摘のリスト）から、n 回中の過半数に現れた指摘を返す。

    失敗した判定は samples に含めないが、n には数える（失敗が多いほど残りにくくする）。
    1回の判定の中の指摘どうしは、同じ束にまとめない。
    attempts は早期終了で判定を打ち切ったときの実際の回数（Voted.samples に入る。既定は n）。
    """
    clusters: list[list[tuple[int, T]]] = []
    for index, items in enumerate(samples):
        for item in items:
            for cluster in clusters:
                if all(i != index for i, _ in cluster) and all(same(item, m) for _, m in cluster):
                    cluster.append((index, item))
                    break
            else:
                clusters.append([(index, item)])
    needed = n // 2 + 1
    return [
        Voted(
            item=cluster[0][1],
            votes=len(cluster),
            members=[m for _, m in cluster],
            samples=attempts or n,
        )
        for cluster in clusters
        if len(cluster) >= needed
    ]


async def vote[T](
    judge: Callable[[], Awaitable[list[T]]],
    *,
    same: Callable[[T, T], bool],
    n: int,
    early_stop: bool = False,
) -> "Ballot[T]":
    """judge を最大 n 回呼んで多数決を取る。"""
    if not early_stop:
        return _finish(await _run(judge, n), same=same, n=n)
    needed = n // 2 + 1
    outcomes = await _run(judge, min(needed, n))
    while len(outcomes) < n and not _decided(outcomes, same=same, n=n):
        outcomes += await _run(judge, 1)
    return _finish(outcomes, same=same, n=n)


async def _run[T](judge: Callable[[], Awaitable[list[T]]], k: int) -> list[list[T] | BaseException]:
    return list(await asyncio.gather(*(judge() for _ in range(k)), return_exceptions=True))


def _decided[T](
    outcomes: list[list[T] | BaseException], *, same: Callable[[T, T], bool], n: int
) -> bool:
    """残りの判定がどう出ても、過半数に残る指摘が変わらないか。"""
    samples = [o for o in outcomes if not isinstance(o, BaseException)]
    if not samples:
        return False  # 1回も成功していなければ続ける
    needed = n // 2 + 1
    remaining = n - len(outcomes)
    return all(
        v.votes >= needed or v.votes + remaining < needed for v in majority(samples, same=same, n=1)
    )


@dataclass(frozen=True)
class Ballot[T]:
    voted: list[Voted[T]]  # 過半数に現れた指摘
    attempts: int  # 実際に行った判定の回数
    errors: list[BaseException]  # 失敗した判定の例外

    @property
    def all_failed(self) -> bool:
        return len(self.errors) == self.attempts


def _finish[T](
    outcomes: list[list[T] | BaseException], *, same: Callable[[T, T], bool], n: int
) -> Ballot[T]:
    samples = [o for o in outcomes if not isinstance(o, BaseException)]
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    voted = majority(samples, same=same, n=n, attempts=len(outcomes))
    return Ballot(voted=voted, attempts=len(outcomes), errors=errors)


def most_common[T](values: list[T]) -> T:
    """最頻値。同数の場合は先に現れたもの。"""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)
