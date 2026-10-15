/**
 * ログインした利用者だけが使える画面で、ゲスト（期限切れを含む）に出す案内。
 * API はゲストに 401 を返すので、問い合わせずにこの案内を出す（アクセス制御そのものは API が行う）。
 */

import type { ReactNode } from "react";

import { loginUrl } from "../lib/auth";
import { Link } from "../lib/router";

export function LoginRequired({ path, children }: { path: string; children: ReactNode }) {
  return (
    <section className="panel login-required" aria-labelledby="login-required-title">
      <h2 id="login-required-title" className="panel-title">
        この画面を使うにはログインが必要です
      </h2>
      <p>{children}</p>
      <div className="login-required-actions">
        <a className="button-primary-link" href={loginUrl(path)}>
          Googleでログイン
        </a>
        <Link href="/" className="button-secondary">
          共通デモの解析結果を見る
        </Link>
      </div>
      <p className="note">
        岡山大学のGoogleアカウントでログインできます。Googleには本人確認（メールアドレスの確認）だけを依頼し、論文や解析結果は送りません。
      </p>
    </section>
  );
}

/** ログインの状態を確かめている間の表示 */
export function AuthChecking() {
  return <p className="note">読み込み中…</p>;
}
