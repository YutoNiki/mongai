import type { ExternalRequest } from "./api";

/** 外部要求の中身を1行で表す（書誌情報・DOI・用語。原稿の文は含まれない） */
export function describeRequest(request: ExternalRequest | string): string {
  if (typeof request === "string") return request;
  if (request.kind === "term_lookup") return `用語「${request.term ?? ""}」（${request.lang ?? ""}）`;
  if (request.kind === "oa_fetch") return `DOI ${request.doi ?? ""}`;
  const parts = [
    request.doi ? `DOI ${request.doi}` : null,
    request.title ? `「${request.title}」` : null,
    request.first_author ?? null,
    request.year ? String(request.year) : null,
  ];
  return parts.filter(Boolean).join(" ／ ");
}
