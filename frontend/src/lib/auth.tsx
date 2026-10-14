/**
 * ログインの状態（ゲストか、ログインした利用者か）。表示のためだけに使い、アクセス制御は core の API が行う。
 *
 * - 起動時に GET /api/me を読む。Cookie はあるが無効・期限切れのとき（401 invalid_session）は「期限切れ」にする
 * - ログインは /auth/login へのページ遷移（Google の画面へ移る）。ログアウトは POST /auth/logout のあと、
 *   画面を読み込み直して、前の利用者の解析結果・PDF が画面に残らないようにする
 * - Google から戻ったときのエラー（?auth_error=…）は表示したあと URL から消す
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, SESSION_INVALID_EVENT, type Me } from "./api";

export type AuthState =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "user"; email: string | null; displayName: string | null }
  | { kind: "expired" }
  | { kind: "unavailable" };

const AUTH_ERRORS: Record<string, string> = {
  denied:
    "このGoogleアカウントではログインできません。岡山大学のGoogleアカウント（okayama-u.ac.jp・そのサブドメインのメールアドレス）でログインしてください。",
  cancelled: "ログインを取り消しました。",
  expired: "ログインの手続きの有効期限が切れたか、手順が正しくありませんでした。もう一度ログインしてください。",
  failed: "Googleでのログインを確認できませんでした。もう一度お試しください。",
  unavailable: "認証サービスに接続できませんでした。しばらくしてからお試しください。",
  not_configured: "Googleでのログインが設定されていません（管理者に連絡してください）。ゲストとして共通デモを閲覧できます。",
};

/** URL の ?auth_error=… を読み、表示する文に変える（読んだら URL から消す） */
function takeAuthError(): string | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (code === null) return null;
  url.searchParams.delete("auth_error");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  return AUTH_ERRORS[code] ?? AUTH_ERRORS.failed ?? null;
}

/** ログインの画面へ（ログイン後に今の画面へ戻る） */
export function loginUrl(path: string = window.location.pathname): string {
  return path && path !== "/" ? `/auth/login?next=${encodeURIComponent(path)}` : "/auth/login";
}

/** ログアウトして、ゲストとして画面を読み込み直す（前の利用者のデータを残さない） */
export async function logout(): Promise<void> {
  await api.logout();
  try {
    window.sessionStorage.clear();
  } catch {
    // sessionStorage が使えない環境では何もしない
  }
  window.location.replace("/");
}

interface AuthContextValue {
  state: AuthState;
  error: string | null;
  dismissError: () => void;
}

const AuthContext = createContext<AuthContextValue>({
  state: { kind: "loading" },
  error: null,
  dismissError: () => undefined,
});

function toState(me: Me): AuthState {
  return me.authenticated ? { kind: "user", email: me.email, displayName: me.display_name } : { kind: "guest" };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" });
  const [error, setError] = useState<string | null>(() => takeAuthError());

  useEffect(() => {
    let active = true;
    api.me().then(
      (me) => active && setState(toState(me)),
      (e: unknown) => {
        if (!active) return;
        setState(e instanceof ApiError && e.status === 401 ? { kind: "expired" } : { kind: "unavailable" });
      },
    );
    // 使っている途中でセッションが切れたら（API が invalid_session を返したら）期限切れにする
    const expire = () => setState({ kind: "expired" });
    window.addEventListener(SESSION_INVALID_EVENT, expire);
    return () => {
      active = false;
      window.removeEventListener(SESSION_INVALID_EVENT, expire);
    };
  }, []);

  const dismissError = useCallback(() => setError(null), []);
  return <AuthContext.Provider value={{ state, error, dismissError }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
