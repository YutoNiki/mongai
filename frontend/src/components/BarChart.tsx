/**
 * 棒グラフ（デジタル庁ガイドブック 4.4〜4.5、5.5）。
 * - 原点は 0、並び順は呼び出し側で意味のある順（件数順・論文の順）にする
 * - 値を棒の近くに直接表示する（色だけに頼らない・フォーカスしなくても数値が読める）
 * - 不要なグリッド線・3D・影・アニメーションは使わない
 * - 代替テキスト、表形式での表示、CSV のダウンロードを用意する
 */

import { BarChart as EBarChart } from "echarts/charts";
import { AriaComponent, GridComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useId, useRef, useState } from "react";

import { toCsv } from "../lib/summary";
import { colors } from "../theme/palette";

echarts.use([EBarChart, GridComponent, AriaComponent, CanvasRenderer]);

export interface BarDatum {
  label: string;
  value: number;
}

interface Props {
  title: string;
  unit: string;
  data: BarDatum[];
  description: string;
  horizontal?: boolean;
  csvName: string;
  onSelect?: (index: number) => void;
}

export function BarChart({ title, unit, data, description, horizontal = false, csvName, onSelect }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [showTable, setShowTable] = useState(false);
  const descId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el || showTable) return;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    const categoryAxis = {
      type: "category" as const,
      data: data.map((d) => d.label),
      inverse: horizontal, // 横棒は上から順に並べる
      axisTick: { show: false },
      axisLabel: { color: colors.text, fontSize: 13, interval: 0, width: horizontal ? 150 : 90, overflow: "break" as const },
      axisLine: { lineStyle: { color: colors.border } },
    };
    const valueAxis = {
      type: "value" as const,
      min: 0, // 原点は必ず 0
      minInterval: 1,
      splitLine: { show: false },
      axisLabel: { color: colors.label },
      name: unit,
      nameTextStyle: { color: colors.label },
    };
    chart.setOption({
      animation: false,
      aria: { enabled: true, label: { description } },
      grid: { left: 8, right: 40, top: 24, bottom: 8, containLabel: true },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxis,
      series: [
        {
          type: "bar",
          data: data.map((d) => d.value),
          itemStyle: { color: colors.chartPrimary },
          barMaxWidth: 36,
          label: {
            show: true,
            position: horizontal ? "right" : "top",
            color: colors.text,
            formatter: (p: { value: unknown }) => `${String(p.value)}${unit}`,
          },
        },
      ],
    });
    if (onSelect) chart.on("click", (p) => onSelect(p.dataIndex));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [data, description, horizontal, unit, showTable, onSelect]);

  const downloadCsv = () => {
    const csv = toCsv(["項目", `件数（${unit}）`], data.map((d) => [d.label, d.value]));
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${csvName}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const height = horizontal ? Math.max(160, data.length * 40 + 40) : 260;
  return (
    <figure className="chart" aria-describedby={descId}>
      <figcaption className="panel-title">{title}</figcaption>
      <p id={descId} className="visually-hidden">
        {description}
      </p>
      {showTable ? (
        <table className="data-table">
          <caption className="visually-hidden">{title}</caption>
          <thead>
            <tr>
              <th scope="col">項目</th>
              <th scope="col" className="num">
                件数
              </th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label}>
                <th scope="row">{d.label}</th>
                <td className="num">
                  {d.value}
                  {unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div ref={ref} role="img" aria-label={`${title}。${description}`} style={{ height }} />
      )}
      <div className="chart-actions">
        <button type="button" className="button-secondary" onClick={() => setShowTable((v) => !v)}>
          {showTable ? "グラフで見る" : "表で見る"}
        </button>
        <button type="button" className="button-secondary" onClick={downloadCsv}>
          CSVをダウンロード
        </button>
      </div>
    </figure>
  );
}
