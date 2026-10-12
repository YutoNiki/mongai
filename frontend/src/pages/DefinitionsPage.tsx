import { CATEGORIES, CATEGORY_LABELS, SHAPE_LABELS, SUBCATEGORY_LABELS, label } from "../lib/labels";
import { annotationColors } from "../theme/palette";

const SUBCATEGORY_TEXT: Record<string, string> = {
  premise_evidence_mismatch: "主張を支えるとして示された根拠が、主張や前提と対応していない",
  leap: "主張に至るまでに必要な前提・説明・手順が書かれていない",
  overclaim: "根拠に比べて主張が強すぎる",
  subject_predicate_mismatch: "文の主語と述語が対応していない",
  ambiguous_reference: "「これ」「本手法」などの指示語が何を指すか一意に決まらない",
  disconnection: "段落が直前の段落から論理的につながっていない",
  section_role_violation: "段落の内容が、属するセクションの役割に合っていない",
};

export function DefinitionsPage() {
  return (
    <div className="page">
      <h1>データの定義</h1>
      <section className="panel">
        <h2 className="panel-title">指摘の種類（カテゴリ）と注釈の形</h2>
        <p>注釈付きPDFでは、種類を色だけでなく注釈の形でも区別しています。</p>
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">カテゴリ</th>
              <th scope="col">注釈の形</th>
              <th scope="col">色</th>
            </tr>
          </thead>
          <tbody>
            {CATEGORIES.map((c) => (
              <tr key={c}>
                <th scope="row">{label(CATEGORY_LABELS, c)}</th>
                <td>{label(SHAPE_LABELS, c)}</td>
                <td>
                  <span className="swatch" style={{ background: annotationColors[c] }} aria-hidden="true" /> {annotationColors[c]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2 className="panel-title">論理・読みやすさの指摘の内容</h2>
        <dl className="definitions">
          {Object.entries(SUBCATEGORY_TEXT).map(([k, v]) => (
            <div key={k}>
              <dt>{label(SUBCATEGORY_LABELS, k)}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="panel">
        <h2 className="panel-title">重大度・確度・件数</h2>
        <dl className="definitions">
          <div>
            <dt>重大</dt>
            <dd>論文の主要な主張の妥当性に関わり、査読でほぼ確実に指摘される</dd>
          </div>
          <div>
            <dt>要修正</dt>
            <dd>主張の理解や説得力を明らかに損ない、修正が必要</dd>
          </div>
          <div>
            <dt>提案</dt>
            <dd>直すと読みやすくなる</dd>
          </div>
          <div>
            <dt>確度</dt>
            <dd>同じ判定を3回行い、3回とも同じ指摘が出たものを「高」、2回のものを「低」としています（1回だけの指摘は表示しません）。</dd>
          </div>
          <div>
            <dt>指摘数</dt>
            <dd>「却下」にした指摘を除いた件数です。前回比は、同じ論文（同じファイル・タイトル）の前回の解析との差です。</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
