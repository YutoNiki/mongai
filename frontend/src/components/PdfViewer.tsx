/**
 * PDF ビューア（PDF.js をバンドル。文字マップ等も自分のサーバーから読む）。
 * 既定は枠の幅に合わせて表示し、拡大すると枠の中で横にもスクロールできる（スマートフォンで文字を読めるように）。
 */

import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

import type { BBox } from "../lib/api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/** 幅に合わせた大きさに対する倍率 */
const ZOOMS = [1, 1.5, 2, 3];
/** 枠を描かないとき（毎回新しい配列を作ると、描き直しが繰り返されるので共有する） */
const NO_BOXES: BBox[] = [];

interface Props {
  url: string;
  page: number;
  boxes?: BBox[];
  color?: string;
  label?: string;
  onPageChange: (page: number) => void;
}

export function PdfViewer({ url, page, boxes = NO_BOXES, color = "#000", label = "", onPageChange }: Props) {
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [zoom, setZoom] = useState(0); // ZOOMS の位置
  const [pageWidth, setPageWidth] = useState<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const task = pdfjs.getDocument({
      url,
      cMapUrl: "/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "/pdfjs/standard_fonts/",
      wasmUrl: "/pdfjs/wasm/",
      iccUrl: "/pdfjs/iccs/",
      enableXfa: false,
    });
    task.promise.then(setDoc, () => setError("PDF を読み込めませんでした"));
    return () => {
      void task.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (!doc || !canvasRef.current || !wrapRef.current) return;
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;
    void doc.getPage(page).then((p) => {
      if (cancelled || !canvasRef.current || !wrapRef.current) return;
      const base = p.getViewport({ scale: 1 });
      const s = Math.min(2, wrapRef.current.clientWidth / base.width) * (ZOOMS[zoom] ?? 1);
      const viewport = p.getViewport({ scale: s });
      const canvas = canvasRef.current;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setScale(s);
      setPageWidth(viewport.width);
      const task = p.render({
        canvas,
        viewport,
        transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
      });
      renderTask = task;
      task.promise.catch(() => undefined);
      // 拡大したときは、該当箇所が枠の中に見えるよう横にスクロールする
      const first = boxes.find((b) => b.page === page);
      if (wrapRef.current) wrapRef.current.scrollLeft = first && zoom > 0 ? Math.max(0, first.x0 * s - 16) : 0;
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [doc, page, zoom, boxes]);

  if (error) return <p role="alert">{error}</p>;
  const pageCount = doc?.numPages ?? 0;
  return (
    <div className="pdf-viewer" id="pdf-viewer">
      <div className="pdf-toolbar">
        <button type="button" className="button-secondary" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          前のページ
        </button>
        <span aria-live="polite">
          {page} / {pageCount || "…"} ページ
        </span>
        <button
          type="button"
          className="button-secondary"
          disabled={pageCount === 0 || page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          次のページ
        </button>
        <span className="pdf-zoom">
          <button
            type="button"
            className="button-secondary"
            disabled={zoom === 0}
            onClick={() => setZoom((z) => Math.max(0, z - 1))}
            aria-label="縮小"
          >
            −
          </button>
          <span aria-live="polite">{zoom === 0 ? "幅に合わせる" : `${(ZOOMS[zoom] ?? 1) * 100}%`}</span>
          <button
            type="button"
            className="button-secondary"
            disabled={zoom === ZOOMS.length - 1}
            onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
            aria-label="拡大"
          >
            ＋
          </button>
        </span>
      </div>
      <div className="pdf-scroll" ref={wrapRef}>
        <div className="pdf-page" style={pageWidth ? { width: pageWidth } : undefined}>
        <canvas ref={canvasRef} aria-label={`論文の ${page} ページ目`} role="img" />
        {boxes
          .filter((b) => b.page === page)
          .map((b, i) => (
            <div
              key={i}
              className="pdf-box"
              title={label}
              style={{
                left: b.x0 * scale - 2,
                top: b.y0 * scale - 2,
                width: (b.x1 - b.x0) * scale + 4,
                height: (b.y1 - b.y0) * scale + 4,
                borderColor: color,
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
