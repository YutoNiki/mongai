import { useEffect, useState } from "react";

import { api } from "./api";

/**
 * 解析 ID → 文書の題名。承認キュー・外部通信ログで、解析 ID ではなく文書の題名を示すために使う
 * （既存の解析の一覧の API から作る。読み込めないときは空のまま＝ID を表示する）
 */
export function useAnalysisTitles(enabled: boolean): ReadonlyMap<string, string> {
  const [titles, setTitles] = useState<ReadonlyMap<string, string>>(() => new Map());
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    api.listAnalyses().then(
      (items) => {
        if (!live) return;
        setTitles(new Map(items.map((i) => [i.analysis_id, `${i.sample ? "（デモ）" : ""}${i.title ?? i.filename ?? i.analysis_id}`])));
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [enabled]);
  return titles;
}
