import { useState, type ReactNode } from "react";

import { loginUrl, logout, useAuth } from "../lib/auth";
import { Link, usePath } from "../lib/router";
import { ApprovalNotice } from "./ApprovalNotice";
import logoUrl from "../assets/brand/logo.svg";

const MENU: [string, string][] = [
  ["/", "ダッシュボード"],
  ["/rules", "ルール"],
  ["/instructions", "課題・提出要件"],
  ["/lab", "専門知識"],
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
  const login = (
    <a className="button-primary-link" href={loginUrl(path)}>
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
            <span className="note">（共通デモの閲覧のみ）</span>
          </span>
          {login}
        </>
      )}
      {state.kind === "user" && (
        <>
          <span className="account-status">
            <span className="note">ログイン中</span> <strong className="account-email">{state.email ?? state.displayName ?? "利用者"}</strong>
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
            <li>
              <a className="external-link" href={FEEDBACK_FORM_URL} target="_blank" rel="noopener noreferrer">
                ご意見・ご要望
                <ExternalLinkIcon />
                <span className="visually-hidden">（新しいタブで開きます）</span>
              </a>
            </li>
          </ul>
        </nav>
        <Account path={path} />
      </header>
      <AuthBanner path={path} />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <ApprovalNotice />
    </>
  );
}
