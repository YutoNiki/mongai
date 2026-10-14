import type { ReactNode } from "react";

import { Link, usePath } from "../lib/router";
import { ApprovalNotice } from "./ApprovalNotice";
import logoUrl from "../assets/brand/logo.svg";

const MENU: [string, string][] = [
  ["/", "ダッシュボード"],
  ["/rules", "ルール"],
  ["/lab", "専門知識"],
  ["/audit", "外部通信ログ"],
  ["/queue", "承認キュー"],
  ["/definitions", "データの定義"],
];

/** 今いる画面のメニューか（論文の解析結果・指摘の詳細は「ダッシュボード」の子ページとみなす） */
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
            alt="SI-NSA"
            width={140}
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
      <ApprovalNotice />
    </>
  );
}
