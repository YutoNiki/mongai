import type { ReactNode } from "react";

import { Link, usePath } from "../lib/router";
import logoUrl from "../assets/header-logo.png";

const MENU: [string, string][] = [
  ["/", "解析の一覧"],
  ["/rules", "ルール"],
  ["/lab", "研究室知識"],
  ["/audit", "外部通信ログ"],
  ["/queue", "承認キュー"],
  ["/definitions", "データの定義"],
];

/** 今いる画面のメニューか（解析のダッシュボード・詳細は「解析の一覧」の配下とみなす） */
function isCurrent(href: string, path: string): boolean {
  if (href === "/") return path === "/" || path.startsWith("/analyses/");
  return path === href || path.startsWith(`${href}/`);
}

export function Layout({ children }: { children: ReactNode }) {
  const path = usePath();
  return (
    <>
      <a className="skip-link" href="#main">
        本文へ移動
      </a>
      <header className="site-header">
        <Link href="/" className="site-logo">
          <img
            src={logoUrl}
            alt="門外不出（MongAI） 学内ローカルLLM論文セルフレビューシステム"
            width={252}
            height={40}
          />
        </Link>
        <nav aria-label="メニュー" className="site-nav-wrap">
          <ul className="site-nav">
            {MENU.map(([href, text]) => (
              <li key={href}>
                <Link href={href} aria-current={isCurrent(href, path) ? "page" : undefined}>
                  {text}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
