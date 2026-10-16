/**
 * 棒グラフ（デジタル庁ガイドブック 4.4〜4.5、5.5）。
 * - 原点は 0、並び順は呼び出し側で意味のある順（件数順・論文の順）にする
 * - 値を棒の近くに直接表示する（色だけに頼らない・フォーカスしなくても数値が読める）
 * - 不要なグリッド線・3D・影・アニメーションは使わない
 * - 代替テキスト、表形式での表示、CSV のダウンロードを用意する
 * - 縦棒で項目名が重なる狭い幅（スマートフォン）では、同じ並び順のまま横棒にする
 * - 文字は画面と同じ定義（theme/typography.css）を使う。項目名・単位は本文の書体、目盛り・値は数値の書体。
 *   Canvas に描くので、書体の読み込みを待ってから描く（フォールバックの書体で描かれたままにしない）
 */

import { BarChart as EBarChart } from "echarts/charts";
import { AriaComponent, GridComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useId, useRef, useState } from "react";

import { toCsv } from "../lib/summary";
import { colors } from "../theme/palette";
import { primaryFamily, readChartTypography, whenFontsLoaded } from "../theme/typography";

/** 縦棒1本あたりに要る幅（rem。これより狭いと項目名が重なる。文字を大きくする設定に合わせて広がる） */
const MIN_COLUMN_WIDTH_REM = 4.5;

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

export function BarChart({ title, unit, data, description, horizontal: horizontalProp = false, csvName, onSelect }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [showTable, setShowTable] = useState(false);
  // 縦棒の項目名が重なる幅か（グラフの枠の幅を見て決める）
  const [cramped, setCramped] = useState(false);
  const horizontal = horizontalProp || cramped;
  const descId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el || horizontalProp) return;
    const minColumn = MIN_COLUMN_WIDTH_REM * readChartTypography().rootFontPx;
    const measure = () => setCramped(el.clientWidth < data.length * minColumn);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [data.length, horizontalProp, showTable]);

  useEffect(() => {
    const el = ref.current;
    if (!el || showTable) return;
    const type = readChartTypography();
    const { fontSize, regular, numericStrong } = type;
    const body = { fontFamily: type.bodyFamily, fontSize, fontWeight: regular };
    const numeric = { fontFamily: type.numericFamily, fontSize, fontWeight: regular };
    // 縦棒の項目名は1本分の幅の中で折り返す（隣の項目名と重ならない）。幅が変わったら合わせ直す
    const labelWidth = () => (horizontal ? 150 : Math.max(40, Math.floor((el.clientWidth * 0.85) / Math.max(1, data.length)) - 6));
    const categoryKey = horizontal ? "yAxis" : "xAxis";
    const categoryAxis = {
      type: "category" as const,
      data: data.map((d) => d.label),
      inverse: horizontal, // 横棒は上から順に並べる
      axisTick: { show: false },
      axisLabel: {
        ...body,
        color: colors.text,
        interval: 0,
        width: labelWidth(),
        overflow: "break" as const,
        // 縦棒では「2 関連研究」のような項目名を、番号と名前の間で改行する（名前の途中で折り返さない）
        formatter: horizontal ? undefined : (value: string) => value.replace(/^(\S+)\s+/, "$1\n"),
      },
      axisLine: { lineStyle: { color: colors.border } },
    };
    const valueAxis = {
      type: "value" as const,
      min: 0, // 原点は必ず 0
      minInterval: 1,
      splitLine: { show: false },
      axisLabel: { ...numeric, color: colors.label },
      name: unit,
      nameTextStyle: { ...body, color: colors.label },
    };
    // 描く字の分だけ書体を読み込む（値の単位は数値の書体に無いので、本文の書体の太字で描かれる）
    const labels = data.map((d) => d.label).join("") + unit;
    const fonts = [
      { font: `${regular} ${fontSize}px ${primaryFamily(type.bodyFamily)}`, text: labels },
      { font: `${numericStrong} ${fontSize}px ${primaryFamily(type.bodyFamily)}`, text: unit },
      { font: `${regular} ${fontSize}px ${primaryFamily(type.numericFamily)}`, text: "0123456789" },
      { font: `${numericStrong} ${fontSize}px ${primaryFamily(type.numericFamily)}`, text: "0123456789" },
    ];

    let chart: echarts.ECharts | undefined;
    let observer: ResizeObserver | undefined;
    let disposed = false;
    void whenFontsLoaded(fonts).then(() => {
      if (disposed) return;
      const instance = echarts.init(el, undefined, { renderer: "canvas" });
      chart = instance;
      instance.setOption({
        animation: false,
        aria: { enabled: true, label: { description } },
        textStyle: body,
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
              ...numeric,
              fontWeight: numericStrong,
              color: colors.text,
              formatter: (p: { value: unknown }) => `${String(p.value)}${unit}`,
            },
          },
        ],
      });
      if (onSelect) instance.on("click", (p) => onSelect(p.dataIndex));
      observer = new ResizeObserver(() => {
        instance.setOption({ [categoryKey]: { axisLabel: { width: labelWidth() } } });
        instance.resize();
      });
      observer.observe(el);
    });
    return () => {
      disposed = true;
      observer?.disconnect();
      chart?.dispose();
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
