import { useEffect, useState } from "react";

import { InstructionComposer } from "../components/InstructionComposer";
import { api } from "../lib/api";
import { loginUrl, useAuth } from "../lib/auth";
import type { InstructionProfile } from "../lib/instructions";

export function InstructionsPage() {
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
    {!signedIn ? <a className="button-primary-link" href={loginUrl("/instructions")}>Googleでログイン</a> : <>
      <section className="panel">
        <h2>保存した設定</h2>
        {profiles.length === 0 && <p>保存した設定はありません。下の入力欄から作成できます。</p>}
        <ul className="instruction-profile-list">{profiles.map((profile) => <li key={profile.profile_id}>
          <strong>{profile.name}</strong>（{profile.scope === "faculty" ? "学部共通" : "講義別"}・版{profile.revision}・{profile.requirements.length}項目）
          <button type="button" className="button-secondary" onClick={() => setEditing(profile)}>編集</button>
          <button type="button" className="button-secondary" onClick={() => void remove(profile)}>削除</button>
        </li>)}</ul>
      </section>
      <section className="panel">
        <h2>{editing ? `「${editing.name}」を編集する` : "設定を作る"}</h2>
        {editing && <button type="button" className="button-secondary" onClick={() => setEditing(undefined)}>新しく作る</button>}
        <InstructionComposer key={`${editing?.profile_id ?? "new"}-${generation}`} documentType="course_report"
          editingProfile={editing} onSaved={() => { setEditing(undefined); setGeneration((g) => g + 1); }} />
      </section>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
