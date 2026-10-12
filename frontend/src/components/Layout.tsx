import type { ReactNode } from "react";

import { Link } from "../lib/router";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main">
        本文へ移動
      </a>
      <header className="site-header">
        <Link href="/" className="site-title">
          門外不出（MonGAI）
        </Link>
        <nav aria-label="メニュー">
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
