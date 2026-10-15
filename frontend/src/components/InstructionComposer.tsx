import { useEffect, useState } from "react";

import { api } from "../lib/api";
import {
  CHECK_LABELS, editRequirementParameters, editRequirementText, type CheckKind, type DocumentType, type InstructionDraft,
  type InstructionProfile, type InstructionSelection, type ProfileScope, type Requirement,
} from "../lib/instructions";

function RequirementEditor({ item, index, disabled, change }: {
  item: Requirement; index: number; disabled: boolean; change: (item: Requirement) => void;
}) {
  const prefix = `確認項目${index + 1}`;
  const numeric = item.kind === "character_count" || item.kind === "reference_count";
  return (
    <fieldset className="requirement-editor" disabled={disabled}>
      <legend>{prefix}：{item.source}</legend>
      <label className="check-label"><input type="checkbox" checked={item.enabled}
        onChange={(e) => change({ ...item, enabled: e.target.checked })} />今回適用する</label>
      <label>確認する内容<textarea aria-label={`${prefix}の内容`} value={item.text} maxLength={1000}
        onChange={(e) => change(editRequirementText(item, e.target.value))} /></label>
      {item.source_excerpt && <p className="note">原文：{item.source_excerpt}</p>}
      <div className="requirement-fields">
        <label>条件の強さ<select aria-label={`${prefix}の強さ`} value={item.strength}
          onChange={(e) => change({ ...item, strength: e.target.value as Requirement["strength"] })}>
          <option value="required">必須</option><option value="recommended">推奨</option>
        </select></label>
        <label>判定する項目<select aria-label={`${prefix}の種類`} value={item.kind}
          onChange={(e) => change({ ...item, kind: e.target.value as CheckKind,
            minimum: null, maximum: null, value: null })}>
          {Object.entries(CHECK_LABELS).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
        </select></label>
        {numeric && <>
          <label>下限<input type="number" min={0} max={1000000} value={item.minimum ?? ""}
            aria-label={`${prefix}の下限`} onChange={(e) => change(editRequirementParameters(item, { minimum: e.target.value === "" ? null : Number(e.target.value) }))} /></label>
          <label>上限<input type="number" min={0} max={1000000} value={item.maximum ?? ""}
            aria-label={`${prefix}の上限`} onChange={(e) => change(editRequirementParameters(item, { maximum: e.target.value === "" ? null : Number(e.target.value) }))} /></label>
        </>}
        {(item.kind === "heading" || item.kind === "style") &&
          <label>{item.kind === "heading" ? "見出し名" : "文体（である／ですます など）"}
            <input value={item.value ?? ""} maxLength={200} aria-label={`${prefix}の指定値`}
              onChange={(e) => change(editRequirementParameters(item, { value: e.target.value }))} /></label>}
        {item.kind === "character_count" && <>
          <label>数える範囲<select value={item.count_scope} aria-label={`${prefix}の文字数範囲`}
            onChange={(e) => change(editRequirementParameters(item, { count_scope: e.target.value as "body" | "all" }))}>
            <option value="body">抽出本文（見出し・参考文献を除く）</option><option value="all">全抽出段落</option>
          </select></label>
          <label className="check-label"><input type="checkbox" checked={item.include_spaces}
            onChange={(e) => change(editRequirementParameters(item, { include_spaces: e.target.checked }))} />空白も数える</label>
        </>}
        {item.kind === "content" && <label>確認する範囲<select value={item.aggregation} aria-label={`${prefix}の確認範囲`}
          onChange={(e) => change({ ...item, aggregation: e.target.value as "all" | "any" })}>
          <option value="all">本文全体に当てはまる条件</option><option value="any">一箇所で満たせる条件</option>
        </select></label>}
      </div>
      {item.kind === "manual" && <p className="note">自動判定は行わず、結果に手元で確認する項目として表示します。</p>}
    </fieldset>
  );
}

export function InstructionComposer({ documentType, onChange, editingProfile, onSaved }: {
  documentType: DocumentType; onChange?: (selection: InstructionSelection) => void;
  editingProfile?: InstructionProfile; onSaved?: () => void;
}) {
  const [profiles, setProfiles] = useState<InstructionProfile[]>([]);
  const [selected, setSelected] = useState<string[]>(editingProfile ? [editingProfile.profile_id] : []);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<InstructionDraft | null>(null);
  const [items, setItems] = useState<Requirement[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState(editingProfile?.name ?? "");
  const [scope, setScope] = useState<ProfileScope>(editingProfile?.scope ?? "course");

  useEffect(() => {
    let live = true;
    api.instructionProfiles().then((result) => { if (live) setProfiles(result); },
      () => { if (live) setError("保存した提出要件を読み込めませんでした。ページを読み込み直してください。"); });
    return () => { live = false; };
  }, []);
  useEffect(() => { onChange?.({ ready: !editingProfile }); }, [onChange, editingProfile]);

  const extracting = draft?.status === "queued" || draft?.status === "running";
  useEffect(() => {
    if (!extracting || !draft) return;
    let live = true;
    const timer = window.setInterval(() => {
      api.instructionDraft(draft.draft_id).then((result) => {
        if (!live) return;
        setDraft(result);
        setItems(result.requirements);
        setExcluded(result.excluded_rule_ids);
      }, () => { if (live) setError("確認項目の進捗を取得できません。ページを読み込み直してください。"); });
    }, 2000);
    return () => { live = false; window.clearInterval(timer); };
  }, [draft, extracting]);

  const invalidate = (hasRequirements: boolean) => {
    setDraft(null); setNotice(null); setError(null);
    onChange?.({ ready: !hasRequirements });
  };
  const changeItem = (index: number, item: Requirement) => {
    setItems(items.map((r, i) => i === index ? item : r));
    if (draft?.confirmed) setDraft({ ...draft, confirmed: false });
    onChange?.({ ready: false }); setNotice(null);
  };
  const prepare = async () => {
    setBusy(true); setError(null); setNotice(null); onChange?.({ ready: false });
    try {
      const chosen = profiles.filter((p) => selected.includes(p.profile_id));
      if (chosen.length !== selected.length) throw new Error("設定を読み込んでから選び直してください");
      const result = await api.createInstructionDraft(documentType, text, chosen);
      setDraft(result); setItems(result.requirements); setExcluded(result.excluded_rule_ids);
    } catch (e) { setError(e instanceof Error ? e.message : "確認項目を作成できませんでした"); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!draft) return;
    setBusy(true); setError(null);
    try {
      const edited = await api.editInstructionDraft(draft, items, excluded);
      setDraft(edited);
      const confirmed = await api.confirmInstructionDraft(edited);
      setDraft(confirmed); setItems(confirmed.requirements);
      onChange?.({ ready: true, instruction_draft_id: confirmed.draft_id, instruction_revision: confirmed.revision });
      setNotice("今回適用する提出要件を確定しました。");
    } catch (e) { setError(e instanceof Error ? e.message : "確認項目を確定できませんでした"); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!draft?.confirmed) return;
    setBusy(true); setError(null);
    try {
      const result = await api.saveInstructionProfile(draft, name, scope, editingProfile);
      setProfiles([result, ...profiles.filter((p) => p.profile_id !== result.profile_id)]);
      setNotice(`「${result.name}」を保存しました。今回の解析条件は確定した内容を使います。`);
      onSaved?.();
    } catch (e) { setError(e instanceof Error ? e.message : "設定を保存できませんでした"); }
    finally { setBusy(false); }
  };

  return (
    <div className="instruction-composer">
      <p className="note">学部・講義の様式や今回の課題に合わせて確認できます。設定は自分だけが利用できます。</p>
      {profiles.length > 0 && <fieldset disabled={busy || extracting}>
        <legend>保存した提出要件</legend>
        {profiles.map((profile) => <label key={profile.profile_id} className="check-label">
          <input type="checkbox" checked={selected.includes(profile.profile_id)} onChange={(e) => {
            const next = e.target.checked ? [...selected, profile.profile_id] : selected.filter((id) => id !== profile.profile_id);
            setSelected(next); invalidate(next.length > 0 || text.trim().length > 0);
          }} />{profile.scope === "faculty" ? "学部共通" : "講義別"}：{profile.name}（版{profile.revision}）
        </label>)}
      </fieldset>}
      <label>今回の課題・提出要件（任意）
        <textarea rows={5} value={text} maxLength={10000} disabled={busy || extracting}
          placeholder="例：2,000字以内、である調。序論・本論・結論で構成し、具体例を挙げて考察する。"
          onChange={(e) => { setText(e.target.value); invalidate(selected.length > 0 || e.target.value.trim().length > 0); }} />
      </label>
      <button type="button" className="button-secondary" disabled={busy || extracting || (!text.trim() && selected.length === 0)} onClick={() => void prepare()}>
        {extracting ? "確認項目を作成中…" : "確認項目を作る"}
      </button>
      {draft?.status === "failed" && <p role="alert" className="error">{draft.error} 入力を変更するか、もう一度「確認項目を作る」を押してください。</p>}
      {draft?.status === "ready" && <div className="instruction-preview">
        <h3>今回適用する確認項目</h3>
        <p className="note">原文と読み取った条件を確認してください。内容の文面を編集すると、判定方法は「内容」に戻ります。必要に応じて判定する項目も選び直してください。</p>
        {items.length === 0 && <p>確認項目が抽出されませんでした。入力を見直すか、追加要件なしとして確定できます。</p>}
        {items.map((item, index) => <RequirementEditor key={item.requirement_id} item={item} index={index}
          disabled={busy} change={(next) => changeItem(index, next)} />)}
        {draft.legacy_rules.length > 0 && <details><summary>今回併用する既存ルール（論文用）</summary>
          <p className="note">課題の条件と矛盾するルールは、今回だけ外せます。</p>
          {draft.legacy_rules.map((rule) => <label key={rule.rule_id} className="check-label">
            <input type="checkbox" disabled={busy} checked={!excluded.includes(rule.rule_id)} onChange={(e) => {
              setExcluded(e.target.checked ? excluded.filter((id) => id !== rule.rule_id) : [...excluded, rule.rule_id]);
              setDraft({ ...draft, confirmed: false }); onChange?.({ ready: false });
            }} />{rule.rule}
          </label>)}
        </details>}
        {draft.conflicts.length > 0 && <div role="alert" className="error">
          {draft.conflicts.map((c, i) => <p key={i}>{c.reason}</p>)}
        </div>}
        <button type="button" disabled={busy || draft.confirmed} onClick={() => void confirm()}>
          {draft.confirmed ? "提出要件は確認済みです" : "この確認項目で確定する"}
        </button>
        {draft.confirmed && <details className="instruction-save"><summary>この条件を次回も使う</summary>
          <p className="note">現在有効な確認項目をすべて、自分用の設定として保存します。</p>
          <label>設定名<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} /></label>
          <label>分類<select value={scope} onChange={(e) => setScope(e.target.value as ProfileScope)}>
            <option value="course">講義別</option><option value="faculty">学部共通</option>
          </select></label>
          <button type="button" disabled={busy || !name.trim() || !items.some((r) => r.enabled)} onClick={() => void save()}>
            {editingProfile ? "設定を更新する" : "設定を保存する"}
          </button>
        </details>}
      </div>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}
