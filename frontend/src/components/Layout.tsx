import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";

import { loginUrl, logout, useAuth } from "../lib/auth";
import { Link, usePath } from "../lib/router";
import { ApprovalNotice } from "./ApprovalNotice";
import logoUrl from "../assets/brand/logo.svg";

/** よく使う画面（ヘッダーに常に並べる） */
const MENU: [string, string][] = [
  ["/", "ダッシュボード"],
  ["/rules", "ルール"],
  ["/instructions", "課題・提出要件"],
  ["/lab", "専門知識"],
];

/** ときどき使う画面（「その他」にまとめる） */
const MORE_MENU: [string, string][] = [
  ["/audit", "外部通信ログ"],
  ["/queue", "承認キュー"],
  ["/definitions", "データの定義"],
];

/** ご意見・ご要望の受付（Google フォーム。リンクを開くだけで、論文などのデータは送らない） */
const FEEDBACK_FORM_URL =
  "https://docs.google.com/forms/d/e/1FAIpQLScdFd-8iPWnure9aQZpIleFLHzCrTbfFfuJxCS_HVbqNCVbvw/viewform?usp=dialog";

/** 外部リンクのアイコン（外部の素材を使わず、SVG で描く） */
function ExternalLinkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** メニューのアイコン（3本線） */
function MenuIcon({ open }: { open: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d={open ? "M6 6l12 12M18 6 6 18" : "M4 7h16M4 12h16M4 17h16"}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** 今いる画面のメニューか（論文の解析結果・指摘の詳細は「ダッシュボード」の子ページとみなす） */
function isCurrent(href: string, path: string): boolean {
  if (href === "/") return path === "/" || path.startsWith("/analyses/");
  return path === href || path.startsWith(`${href}/`);
}

/** ヘッダーの右側：ゲストか、ログインした利用者か（表示だけ。アクセス制御は API が行う） */
function Account({ path }: { path: string }) {
  const { state } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signOut = async () => {
    setBusy(true);
    setError(null);
    try {
      await logout();
    } catch (e) {
      setError(e instanceof Error ? e.message : "ログアウトできませんでした");
      setBusy(false);
    }
  };
  // 狭い画面ではヘッダーの行に別のログインのボタンを出すので、ここのボタンは隠す（CSS の account-login）
  const login = (
    <a className="button-primary-link account-login" href={loginUrl(path)}>
      Googleでログイン
    </a>
  );
  return (
    <div className="account" aria-live="polite">
      {state.kind === "loading" && <span className="note">確認中…</span>}
      {state.kind === "guest" && (
        <>
          <span className="account-status">
            <strong>ゲスト</strong>
            <span className="note account-hint">（共通デモの閲覧のみ）</span>
          </span>
          {login}
        </>
      )}
      {state.kind === "user" && (
        <>
          <span className="account-status">
            <span className="note">ログイン中</span>{" "}
            <strong className="account-email" title={state.email ?? state.displayName ?? undefined}>
              {state.email ?? state.displayName ?? "利用者"}
            </strong>
          </span>
          <button type="button" className="button-secondary" onClick={() => void signOut()} disabled={busy}>
            ログアウト
          </button>
        </>
      )}
      {state.kind === "expired" && (
        <>
          <span className="account-status">
            <strong>ログインの有効期限切れ</strong>
          </span>
          {login}
          <button type="button" className="button-secondary" onClick={() => void signOut()} disabled={busy}>
            ゲストに戻る
          </button>
        </>
      )}
      {state.kind === "unavailable" && <span className="note">ログインの状態を確認できません</span>}
      {error && (
        <span role="alert" className="error">
          {error}
        </span>
      )}
    </div>
  );
}

/** ログインの結果・セッションの失効の知らせ */
function AuthBanner({ path }: { path: string }) {
  const { state, error, dismissError } = useAuth();
  if (state.kind === "expired") {
    return (
      <div role="alert" className="auth-banner">
        <p>
          ログインの有効期限が切れたか、ログインの情報が正しくありません。自分の論文・解析結果を使うには、もう一度ログインしてください。
          ゲストに戻ると、共通デモを閲覧できます。
        </p>
        <a className="button-primary-link" href={loginUrl(path)}>
          もう一度ログイン
        </a>
      </div>
    );
  }
  if (state.kind === "unavailable") {
    return (
      <div role="alert" className="auth-banner">
        <p>認証サービスに接続できません。しばらくしてから画面を読み込み直してください。</p>
      </div>
    );
  }
  if (!error) return null;
  return (
    <div role="alert" className="auth-banner">
      <p>{error}</p>
      <button type="button" className="button-secondary" onClick={dismissError}>
        閉じる
      </button>
    </div>
  );
}

/** 狭い画面で、ヘッダーの行に出すログインのボタン（ゲスト・期限切れのとき） */
function QuickLogin({ path }: { path: string }) {
  const { state } = useAuth();
  if (state.kind !== "guest" && state.kind !== "expired") return null;
  return (
    <a className="button-primary-link quick-login" href={loginUrl(path)} aria-label="Googleでログイン">
      ログイン
    </a>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const path = usePath();
  // 狭い画面のメニュー（開閉）と、広い画面の「その他」の一覧（開閉）
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const closeMenus = () => {
    setMenuOpen(false);
    setMoreOpen(false);
  };
  // 画面を移ったら閉じる（戻る・進むでも）
  useEffect(() => {
    setMenuOpen(false);
    setMoreOpen(false);
  }, [path]);
  // 「その他」は、外側を押したら閉じる
  useEffect(() => {
    if (!moreOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!moreRef.current?.contains(event.target as Node)) setMoreOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [moreOpen]);
  // Esc で閉じて、開いたボタンにフォーカスを戻す
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    if (moreOpen) {
      setMoreOpen(false);
      moreButton.current?.focus();
    } else if (menuOpen) {
      setMenuOpen(false);
      menuButton.current?.focus();
    }
  };
  // キーボードで「その他」の外へフォーカスが移ったら閉じる
  const onMoreBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setMoreOpen(false);
  };
  const moreCurrent = MORE_MENU.some(([href]) => isCurrent(href, path));
  const item = ([href, text]: [string, string]) => (
    <li key={href}>
      <Link href={href} aria-current={isCurrent(href, path) ? "page" : undefined} onClick={closeMenus}>
        {text}
      </Link>
    </li>
  );
  return (
    <>
      <a className="skip-link" href="#main">
        本文へ移動
      </a>
      <header className="site-header" onKeyDown={onKeyDown}>
        <div className="site-header-bar">
          <Link href="/" className="site-logo">
            <img
              src={logoUrl}
              alt="SI-NSA"
              width={140}
              height={40}
            />
          </Link>
          <div className="site-header-quick">
            <QuickLogin path={path} />
            <button
              ref={menuButton}
              type="button"
              className="menu-toggle"
              aria-expanded={menuOpen}
              aria-controls="site-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <MenuIcon open={menuOpen} />
              <span>メニュー</span>
            </button>
          </div>
        </div>
        <div id="site-menu" className={menuOpen ? "site-menu open" : "site-menu"}>
          <nav aria-label="メニュー" className="site-nav-wrap">
            <ul className="site-nav">{MENU.map(item)}</ul>
            <div className={moreOpen ? "nav-more open" : "nav-more"} ref={moreRef} onBlur={onMoreBlur}>
              <button
                ref={moreButton}
                type="button"
                className={moreCurrent ? "nav-more-toggle is-current" : "nav-more-toggle"}
                aria-expanded={moreOpen}
                aria-controls="nav-more-list"
                onClick={() => setMoreOpen((open) => !open)}
              >
                その他
                {moreCurrent && <span className="visually-hidden">（表示中の画面を含む）</span>}
                <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <p className="nav-more-heading" aria-hidden="true">
                その他
              </p>
              <ul id="nav-more-list" className="nav-more-list">
                {MORE_MENU.map(item)}
                <li>
                  <a
                    className="external-link"
                    href={FEEDBACK_FORM_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={closeMenus}
                  >
                    ご意見・ご要望
                    <ExternalLinkIcon />
                    <span className="visually-hidden">（新しいタブで開きます）</span>
                  </a>
                </li>
              </ul>
            </div>
          </nav>
          <Account path={path} />
        </div>
      </header>
      <AuthBanner path={path} />
      <ApprovalNotice />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </>
  );
}
