/**
 * ルールの画面（SPEC 3.2-3）：ルール資料の登録と、抽出したルールの確認・編集・承認・無効化。
 * 承認したルールだけが解析に使われる。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { api, type Rule, type RuleDocument, type RuleStatus } from "../lib/api";
import {
  CHECK_TYPE_LABELS,
  RULE_STATUS_LABELS,
  TARGET_SECTIONS,
  TARGET_SECTION_LABELS,
  label,
} from "../lib/labels";

const DOCUMENT_STATUS: Record<string, string> = {
  queued: "順番待ち",
  running: "抽出中",
  completed: "完了",
  failed: "失敗",
};
const ACTIVE = new Set(["queued", "running"]);
const REFRESH_MS = 5_000;

function sourceText(rule: Rule): string {
  const where = (s: { doc: string; page: number | null }) => (s.page ? `${s.doc} p.${s.page}` : s.doc);
  const merged = rule.merged_sources.length > 0 ? `（ほか ${rule.merged_sources.map(where).join("、")}）` : "";
  return where(rule.source) + merged;
}

/** 1件のルールの編集（文面・対象・判定方法）。組み込みのルールは編集できない */
function RuleEditor({ rule, onSaved, onCancel }: { rule: Rule; onSaved: (r: Rule) => void; onCancel: () => void }) {
  const [text, setText] = useState(rule.rule);
  const [section, setSection] = useState(rule.target_section);
  const [checkType, setCheckType] = useState(rule.check_type);
  const [error, setError] = useState<string | null>(null);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onSaved(await api.updateRule(rule.rule_id, { rule: text, target_section: section, check_type: checkType }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存できませんでした");
    }
  };
  return (
    <form className="rule-editor" onSubmit={save}>
      <label>
        ルール
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000} required />
      </label>
      <label>
        対象
        <select value={section} onChange={(e) => setSection(e.target.value)}>
          {TARGET_SECTIONS.map((s) => (
            <option key={s} value={s}>
              {label(TARGET_SECTION_LABELS, s)}
            </option>
          ))}
        </select>
      </label>
      <label>
        判定方法
        <select value={checkType} onChange={(e) => setCheckType(e.target.value as Rule["check_type"])}>
          <option value="llm">{CHECK_TYPE_LABELS.llm}</option>
          <option value="advisory">{CHECK_TYPE_LABELS.advisory}</option>
          {rule.checker && <option value="mechanical">{CHECK_TYPE_LABELS.mechanical}</option>}
        </select>
      </label>
      <div className="status-buttons">
        <button type="submit">保存</button>
        <button type="button" className="button-secondary" onClick={onCancel}>
          取り消す
        </button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
    </form>
  );
}

export function RulesPage() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [documents, setDocuments] = useState<RuleDocument[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | RuleStatus>("");
  const [editing, setEditing] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    api.rules().then(setRules, () => setMessage("ルールを読み込めませんでした"));
    api.ruleDocuments().then(setDocuments, () => setDocuments([]));
  }, []);
  useEffect(load, [load]);
  const extracting = documents.some((d) => ACTIVE.has(d.status));
  useEffect(() => {
    if (!extracting) return;
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [extracting, load]);

  const shown = useMemo(
    () => (rules ?? []).filter((r) => !statusFilter || r.status === statusFilter),
    [rules, statusFilter],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = { pending: 0, approved: 0, disabled: 0 };
    for (const r of rules ?? []) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rules]);

  const replace = (updated: Rule[]) =>
    setRules((list) => (list ?? []).map((r) => updated.find((u) => u.rule_id === r.rule_id) ?? r));

  const setStatus = async (rule: Rule, status: RuleStatus) => {
    try {
      replace([await api.updateRule(rule.rule_id, { status })]);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "更新できませんでした");
    }
  };

  const approveShown = async () => {
    const ids = shown.filter((r) => r.status === "pending").map((r) => r.rule_id);
    if (ids.length === 0) return;
    if (!window.confirm(`表示中の未確認のルール ${ids.length} 件をすべて承認しますか？`)) return;
    try {
      replace(await api.bulkRuleStatus(ids, "approved"));
      setMessage(`${ids.length} 件のルールを承認しました`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "承認できませんでした");
    }
  };

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const doc = await api.uploadRuleDocument(file);
      setMessage(`「${doc.filename}」を登録しました。ルールの抽出が終わると、未確認のルールとして一覧に加わります`);
      setFile(null);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "登録できませんでした");
    } finally {
      setBusy(false);
    }
  };

  const removeDocument = async (doc: RuleDocument) => {
    if (!window.confirm(`「${doc.filename}」と、そこから抽出したルール ${doc.rule_count} 件を削除しますか？`)) return;
    try {
      await api.deleteRuleDocument(doc.document_id);
      load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "削除できませんでした");
    }
  };

  return (
    <div className="page">
      <h1>ルール</h1>
      <div className="grid">
        <section className="panel kpi" aria-labelledby="rule-count-title">
          <h2 id="rule-count-title" className="panel-title">
            解析に使うルール（承認済み）
          </h2>
          <p className="kpi-value">
            {counts.approved}
            <span className="kpi-unit">件</span>
          </p>
          <dl className="kpi-breakdown two">
            <div>
              <dt>未確認</dt>
              <dd>{counts.pending}件</dd>
            </div>
            <div>
              <dt>無効</dt>
              <dd>{counts.disabled}件</dd>
            </div>
          </dl>
        </section>
        <section className="panel summary-text" aria-labelledby="rule-upload-title">
          <h2 id="rule-upload-title" className="panel-title">
            ルール資料を登録する
          </h2>
          <p className="note">
            投稿規定・ライティングガイド・報告ガイドラインなど（PDF・Markdown・テキスト）。資料から書き方のルールを取り出し、
            「未確認」として一覧に加えます。内容を確認して承認したルールだけが、解析で使われます。資料は学外に送信されません。
          </p>
          <form onSubmit={upload} className="upload-form">
            <label>
              ルール資料
              <input
                type="file"
                accept=".pdf,.md,.markdown,.txt,application/pdf,text/markdown,text/plain"
                required
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <button type="submit" disabled={!file || busy}>
              {busy ? "登録中…" : "登録してルールを抽出する"}
            </button>
          </form>
        </section>
      </div>
      {message && <p aria-live="polite">{message}</p>}

      {documents.length > 0 && (
        <section className="panel" aria-labelledby="rule-docs-title">
          <h2 id="rule-docs-title" className="panel-title">
            登録したルール資料
          </h2>
          <div className="table-scroll">
            <table className="data-table responsive">
              <thead>
                <tr>
                  <th scope="col">資料</th>
                  <th scope="col">状態</th>
                  <th scope="col" className="num">
                    ルール（件）
                  </th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.document_id}>
                    <td data-label="資料">{d.filename}</td>
                    <td data-label="状態">
                      {label(DOCUMENT_STATUS, d.status)}
                      {d.error && <span className="sub">（{d.error}）</span>}
                    </td>
                    <td className="num" data-label="ルール（件）">
                      {d.rule_count}
                    </td>
                    <td data-label="操作">
                      <button
                        type="button"
                        className="button-secondary"
                        disabled={ACTIVE.has(d.status)}
                        onClick={() => void removeDocument(d)}
                      >
                        削除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel" aria-labelledby="rules-title">
        <h2 id="rules-title" className="panel-title">
          ルールの一覧（{shown.length}件）
        </h2>
        <form className="filters" aria-label="絞り込み" onSubmit={(e) => e.preventDefault()}>
          <label>
            状態
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as "" | RuleStatus)}>
              <option value="">すべて</option>
              {Object.entries(RULE_STATUS_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="button-secondary"
            disabled={!shown.some((r) => r.status === "pending")}
            onClick={() => void approveShown()}
          >
            表示中の未確認をすべて承認
          </button>
        </form>
        {rules === null ? (
          <p>読み込み中…</p>
        ) : shown.length === 0 ? (
          <p>条件に合うルールはありません。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table responsive rules-table">
              <caption className="visually-hidden">ルールの一覧</caption>
              <thead>
                <tr>
                  <th scope="col">状態</th>
                  <th scope="col">対象</th>
                  <th scope="col">ルール</th>
                  <th scope="col">判定方法</th>
                  <th scope="col">出典</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.rule_id}>
                    <td data-label="状態">
                      <span className={`badge rule-${r.status}`}>{label(RULE_STATUS_LABELS, r.status)}</span>
                    </td>
                    <td data-label="対象">{label(TARGET_SECTION_LABELS, r.target_section)}</td>
                    <td data-label="ルール">
                      {editing === r.rule_id ? (
                        <RuleEditor
                          rule={r}
                          onSaved={(u) => {
                            replace([u]);
                            setEditing(null);
                          }}
                          onCancel={() => setEditing(null)}
                        />
                      ) : (
                        r.rule
                      )}
                    </td>
                    <td data-label="判定方法">{label(CHECK_TYPE_LABELS, r.check_type)}</td>
                    <td data-label="出典">{sourceText(r)}</td>
                    <td className="status-buttons" data-label="操作">
                      {r.status !== "approved" && (
                        <button type="button" onClick={() => void setStatus(r, "approved")}>
                          承認
                        </button>
                      )}
                      {r.status !== "disabled" && (
                        <button type="button" className="button-secondary" onClick={() => void setStatus(r, "disabled")}>
                          無効にする
                        </button>
                      )}
                      {!r.builtin && editing !== r.rule_id && (
                        <button type="button" className="button-secondary" onClick={() => setEditing(r.rule_id)}>
                          編集
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
