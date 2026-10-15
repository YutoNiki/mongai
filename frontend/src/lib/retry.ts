/**
 * 失敗した解析のやり直し。アップロード済みの同じ文書を、同じ設定（指摘を書く言語・文書の種類）で解析し直す。
 * 既存の API（解析の集計・提出要件のドラフト・解析の開始）だけを使う。
 *
 * 提出要件を付けた解析は、解析を始めたときのドラフトが今も同じ内容で確定済みのときだけ、そのドラフトでやり直す。
 * ドラフトが後から変わっていたら、黙って違う条件で解析しないよう、理由を知らせて止める。
 */

import { api } from "./api";
import type { AnalysisOptions, InstructionSnapshot } from "./instructions";

/** 解析を始めたときに固定した提出要件（API は元のドラフトの ID も返す） */
type SnapshotWithDraft = InstructionSnapshot & { draft_id?: string | null };

export class RetryError extends Error {}

const CHANGED =
  "提出要件の確認項目が、この解析のあとに変更されたため、同じ条件ではやり直せません。トップページのフォームで確認項目を確定してから、文書をアップロードして解析してください。";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** 失敗した解析をやり直し、新しい解析の ID を返す */
export async function retryAnalysis(analysisId: string): Promise<string> {
  const { analysis } = await api.summary(analysisId);
  const { settings } = analysis;
  const outputLang = settings.output_lang === "ja" ? "ja" : "paper";
  const options: AnalysisOptions = { document_type: settings.document_type ?? "paper" };
  const snapshot = settings.instruction_snapshot as SnapshotWithDraft | null | undefined;
  if (snapshot) {
    if (!snapshot.draft_id) throw new RetryError(CHANGED);
    const draft = await api.instructionDraft(snapshot.draft_id);
    const unchanged =
      draft.confirmed &&
      draft.document_type === snapshot.document_type &&
      same(draft.requirements, snapshot.requirements) &&
      same(draft.excluded_rule_ids, snapshot.excluded_rule_ids);
    if (!unchanged) throw new RetryError(CHANGED);
    options.instruction_draft_id = draft.draft_id;
    options.instruction_revision = draft.revision;
  }
  const started = await api.startAnalysis(analysis.doc_id, outputLang, options);
  return started.analysis.analysis_id;
}
