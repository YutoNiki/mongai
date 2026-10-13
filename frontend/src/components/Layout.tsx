import type { ReactNode } from "react";

import { Link } from "../lib/router";
import logoUrl from "../assets/header-logo.png";

export function Layout({ children }: { children: ReactNode }) {
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
            <li>
              <Link href="/">解析の一覧</Link>
            </li>
            <li>
              <Link href="/audit">外部通信ログ</Link>
            </li>
            <li>
              <Link href="/queue">承認キュー</Link>
            </li>
            <li>
              <Link href="/definitions">データの定義</Link>
            </li>
          </ul>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
