"""おいとまAPIの同時リクエスト数と処理量の関係を測る（make bench-concurrency）。

同時に送る本数を段階的に増やし、全体の出力の速さ（トークン/秒）が頭打ちになる本数を調べる。
config/models.yaml の api.max_concurrency を決める材料にする。送るのは合成したプロンプトだけで、
論文や解析結果は使わない。解析が動いている間に測ると、結果がずれる。

    python -m core.llm.bench --levels 1,4,8,16,32 --role logic_check --max-tokens 768
"""

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass

from core.llm.client import OitomaClient, OitomaError
from core.llm.config import get_models_config

# 解析の段落チェックに近い長さ（約1,500トークン）の合成した入力
_FILLER = "これは処理量の計測のために合成した文章であり，特定の論文の内容を含まない．" * 40
_TOPICS = ["川の流れ", "季節の変化", "図書館", "鉄道", "天気予報", "料理", "庭づくり", "地図"]


@dataclass(frozen=True)
class LevelResult:
    concurrency: int
    wall_s: float
    ok: int
    failed: int
    completion_tokens: int
    latencies: list[float]

    @property
    def throughput(self) -> float:
        return self.completion_tokens / self.wall_s if self.wall_s else 0.0

    @property
    def per_request(self) -> float:
        """1本あたりの出力の速さ（トークン/秒）。"""
        return self.throughput / self.ok if self.ok else 0.0


async def run_level(
    client: OitomaClient, *, concurrency: int, model: str, max_tokens: int, thinking: bool | None
) -> LevelResult:
    async def one(i: int) -> tuple[int, float] | None:
        topic = _TOPICS[i % len(_TOPICS)]
        messages = [
            {"role": "system", "content": "あなたは文章を書く助手です。"},
            {
                "role": "user",
                "content": f"[{i}] {_FILLER}\n\n上の文章とは関係なく、「{topic}」について"
                "できるだけ長く、詳しい説明文を日本語で書いてください。",
            },
        ]
        started = time.monotonic()
        try:
            result = await client.chat(
                messages,
                model=model,
                temperature=0.7,
                max_tokens=max_tokens,
                thinking=thinking,
                role="bench",
            )
        except OitomaError:
            return None
        return result.completion_tokens or 0, time.monotonic() - started

    started = time.monotonic()
    outcomes = await asyncio.gather(*(one(i) for i in range(concurrency)))
    wall = time.monotonic() - started
    done = [o for o in outcomes if o is not None]
    return LevelResult(
        concurrency=concurrency,
        wall_s=wall,
        ok=len(done),
        failed=len(outcomes) - len(done),
        completion_tokens=sum(t for t, _ in done),
        latencies=[s for _, s in done],
    )


def format_table(results: list[LevelResult]) -> str:
    lines = [
        "| 同時数 | 所要(秒) | 成功 | 失敗 | 出力トークン | 全体(tok/s) | 1本あたり(tok/s) "
        "| 応答時間 平均/最大(秒) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        mean = statistics.mean(r.latencies) if r.latencies else 0.0
        worst = max(r.latencies, default=0.0)
        lines.append(
            f"| {r.concurrency} | {r.wall_s:.1f} | {r.ok} | {r.failed} | {r.completion_tokens} "
            f"| {r.throughput:.1f} | {r.per_request:.1f} | {mean:.1f} / {worst:.1f} |"
        )
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", default="1,4,8,16,32")
    parser.add_argument("--role", default="logic_check", help="モデルを取る役割")
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--thinking", action="store_true", help="思考モードをオンにする")
    args = parser.parse_args(argv)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]

    config = get_models_config()
    model = config.role(args.role).model
    # クライアント側の同時数の上限で頭打ちにならないよう、最大の同時数に合わせる
    api = config.api.model_copy(update={"max_concurrency": max(levels), "max_retries": 0})
    config = config.model_copy(update={"api": api})
    print(f"モデル：{model}　出力の上限：{args.max_tokens}　思考：{args.thinking}")
    results: list[LevelResult] = []
    async with OitomaClient(config) as client:
        for level in levels:
            result = await run_level(
                client,
                concurrency=level,
                model=model,
                max_tokens=args.max_tokens,
                thinking=args.thinking,
            )
            results.append(result)
            print(
                f"同時数 {level}: {result.throughput:.1f} tok/s"
                f"（成功 {result.ok}・失敗 {result.failed}・{result.wall_s:.0f} 秒）",
                flush=True,
            )
    print()
    print(format_table(results))


if __name__ == "__main__":
    asyncio.run(main())
