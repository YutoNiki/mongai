import { useEffect } from "react";

/** ブラウザのタブ・履歴に出る題名（「画面名｜SI-NSA」）。title が無い間は変えない */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    if (title) document.title = `${title}｜SI-NSA`;
  }, [title]);
}
