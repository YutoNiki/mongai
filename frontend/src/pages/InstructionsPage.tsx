import { useEffect, useState } from "react";

import { InstructionComposer } from "../components/InstructionComposer";
import { AuthChecking, LoginRequired } from "../components/LoginRequired";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { InstructionProfile } from "../lib/instructions";
import { useDocumentTitle } from "../lib/title";

export function InstructionsPage() {
  useDocumentTitle("課題・提出要件");
  const { state: auth } = useAuth();
  const [profiles, setProfiles] = useState<InstructionProfile[]>([]);
  const [editing, setEditing] = useState<InstructionProfile | undefined>();
  const [generation, setGeneration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const signedIn = auth.kind === "user";
  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    api.instructionProfiles().then((result) => { if (live) setProfiles(result); },
      () => { if (live) setError("保存した提出要件を読み込めませんでした"); });
    return () => { live = false; };
  }, [signedIn, generation]);
  const remove = async (profile: InstructionProfile) => {
    if (!window.confirm(`「${profile.name}」を削除しますか？ 過去の解析に保存された適用条件は残ります。`)) return;
    try {
      await api.deleteInstructionProfile(profile.profile_id);
      setEditing(undefined); setGeneration((g) => g + 1); setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : "削除できませんでした"); }
  };
  return <div className="page">
    <h1>課題・提出要件</h1>
    <p>学部共通の様式や講義ごとの条件を、自分用に保存して解析時に選べます。</p>
    {auth.kind === "loading" ? <AuthChecking /> : !signedIn ? (
      <LoginRequired path="/instructions">
        学部共通の様式や講義ごとの提出要件を保存しておき、レポートを解析するときに選んで確認できます。設定は利用者ごとに保存します。
      </LoginRequired>
    ) : <>
      <section className="panel" aria-labelledby="profiles-title">
        <h2 id="profiles-title" className="panel-title">保存した設定</h2>
        {profiles.length === 0 && <p>保存した設定はありません。下の入力欄から作成できます。</p>}
        <ul className="instruction-profile-list">{profiles.map((profile) => <li key={profile.profile_id}>
          <strong>{profile.name}</strong>（{profile.scope === "faculty" ? "学部共通" : "講義別"}・版{profile.revision}・{profile.requirements.length}項目）
          <button type="button" className="button-secondary" onClick={() => setEditing(profile)}>編集</button>
          <button type="button" className="button-secondary" onClick={() => void remove(profile)}>削除</button>
        </li>)}</ul>
      </section>
      <section className="panel" aria-labelledby="profile-editor-title">
        <h2 id="profile-editor-title" className="panel-title">{editing ? `「${editing.name}」を編集する` : "設定を作る"}</h2>
        {editing && <button type="button" className="button-secondary" onClick={() => setEditing(undefined)}>新しく作る</button>}
        <InstructionComposer key={`${editing?.profile_id ?? "new"}-${generation}`} documentType="course_report"
          editingProfile={editing} onSaved={() => { setEditing(undefined); setGeneration((g) => g + 1); }} />
      </section>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
