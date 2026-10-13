/** 最小限のルーター（History API）。外部ライブラリを増やさないため自前で持つ。 */

import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";
import { flushSync } from "react-dom";

/** 画面の切り替えの動き：forward は左へ送る（次の画面が右から入る）、back は右へ戻す */
export type Transition = "forward" | "back";

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => { finished: Promise<void> };
};

const listeners = new Set<() => void>();

// 履歴の位置（戻る・進むで、どちら向きに動かすかを決める）
let position = (window.history.state as { position?: number } | null)?.position ?? 0;
window.history.replaceState({ position }, "");

function render(update: () => void, transition?: Transition): void {
  const doc = document as ViewTransitionDocument;
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  // View Transitions に対応していないブラウザや、動きを減らす設定では、動かさずに切り替える
  if (!transition || !doc.startViewTransition || reduced) {
    update();
    return;
  }
  document.documentElement.dataset.transition = transition;
  const done = () => {
    delete document.documentElement.dataset.transition;
  };
  doc.startViewTransition(() => flushSync(update)).finished.then(done, done);
}

function notify(): void {
  for (const listener of listeners) listener();
}

export function navigate(path: string, transition?: Transition): void {
  position += 1;
  window.history.pushState({ position }, "", path);
  render(() => {
    notify();
    window.scrollTo(0, 0);
  }, transition);
}

window.addEventListener("popstate", (event: PopStateEvent & { hasUAVisualTransition?: boolean }) => {
  const next = (event.state as { position?: number } | null)?.position ?? 0;
  const transition: Transition = next < position ? "back" : "forward";
  position = next;
  // スマートフォンのスワイプで戻るときは、ブラウザ自身が動かすので重ねない
  render(notify, event.hasUAVisualTransition ? undefined : transition);
});

export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    listeners.add(update);
    return () => {
      listeners.delete(update);
    };
  }, []);
  return path;
}

export function matchRoute(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split("/").filter(Boolean);
  const s = path.split("/").filter(Boolean);
  if (p.length !== s.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < p.length; i++) {
    const part = p[i] ?? "";
    const value = s[i] ?? "";
    if (part.startsWith(":")) params[part.slice(1)] = decodeURIComponent(value);
    else if (part !== value) return null;
  }
  return params;
}

export function Link({
  href,
  onClick,
  transition,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; transition?: Transition }) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(href, transition);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
