// 업체 도우미 HTTP 계약은 서버 OpenAPI에서 생성한 타입을 사용해요.
import type { components } from "@/lib/backend/schema";

type Wire = components["schemas"];
export const FORM_FIELDS = {
  period: ["start", "end", "excluded"],
  quote: ["amount", "vat", "days", "valid"],
  dates: ["days", "crew"],
  extra: ["title", "reason", "amount", "vat"],
} as const;

export type AssistForm = Wire["FormName"];
export type AssistValue = Wire["Value"];
export type AssistCurrent = Record<string, AssistValue>;
export type AssistAction = Wire["Action"];
export type AssistSource = Wire["Source"];
export type FieldIntent = Wire["FieldIntent"];
export type Alternative = Wire["Alternative"];
export type CandidateField = Wire["CandidateField"];
export type PreviousCandidate = Wire["PreviousCandidate"];
export type AssistRequest = Wire["AssistRequest"];
export type AssistResponse = Wire["AssistResponse"];
export type RuleOptions = {
  today: string;
  baseMonth?: string | null;
  holidays?: string[];
  noWeekend?: boolean;
  vatModes?: readonly string[];
  daysMax?: number;
  crewMax?: number;
  requiredDays?: number | null;
  immutableFields?: readonly string[];
};

export function formKeys(form: AssistForm): readonly string[] {
  return FORM_FIELDS[form];
}

export function copyCurrent(form: AssistForm, current: AssistCurrent): AssistCurrent {
  return Object.fromEntries(formKeys(form).map((field) => {
    const value = current[field];
    return [field, Array.isArray(value) ? [...value] : value ?? null];
  }));
}

export function sameValue(a: AssistValue, b: AssistValue): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => item === b[i]);
  }
  return a === b;
}

export function keepField(field: string): FieldIntent {
  return { field, action: "keep", value: null, evidence: "" };
}

export function emptyCandidate(field: string): CandidateField {
  return { ...keepField(field), status: "uncertain", source: "rule", alternatives: [] };
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + "T00:00:00Z");
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

// 서버도 업무 제약을 다시 검증해요. 이 검사는 응답을 폼에 넣기 전의 화면 안전 장치예요.
export function validSetValue(form: AssistForm, field: string, value: unknown, opts: RuleOptions): value is AssistValue {
  if (!formKeys(form).includes(field) || opts.immutableFields?.includes(field)) return false;
  if (field === "excluded" || (form === "dates" && field === "days")) {
    const max = form === "dates" ? (opts.daysMax ?? 120) : 31;
    if (!Array.isArray(value) || value.length > max || !value.every((day) => typeof day === "string" && isIsoDate(day))) return false;
    if (new Set(value).size !== value.length) return false;
    if (form === "dates") {
      if (!value.length || value.some((day) => day <= opts.today || opts.holidays?.includes(day))) return false;
      if (value.some((day) => Date.parse(day) - Date.parse(opts.today) > 365 * 86_400_000)) return false;
      if (opts.noWeekend && value.some((day) => [0, 6].includes(new Date(day + "T00:00:00Z").getUTCDay()))) return false;
      if (opts.requiredDays && value.length !== opts.requiredDays) return false;
      const ordered = [...value].sort();
      if ((Date.parse(ordered.at(-1)!) - Date.parse(ordered[0])) / 86_400_000 > 120) return false;
    }
    return true;
  }
  if (field === "amount") {
    return typeof value === "number" && Number.isSafeInteger(value) && value > 0 &&
      value <= 1_000_000_000;
  }
  if (field === "crew" || (form === "quote" && field === "days")) {
    const max = field === "crew" ? (opts.crewMax ?? 6) : (opts.daysMax ?? 365);
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= max;
  }
  if (field === "vat") {
    return typeof value === "string" && ["included", "excluded", "exempt"].includes(value) &&
      (!opts.vatModes?.length || opts.vatModes.includes(value));
  }
  if (field === "start" || field === "end" || field === "valid") {
    return typeof value === "string" && isIsoDate(value) &&
      (form === "period" ? value > opts.today : value >= opts.today);
  }
  if (field === "title" || field === "reason") {
    const max = field === "title" ? 60 : 200;
    return typeof value === "string" && value.trim().length > 0 && value.length <= max;
  }
  return false;
}

function isValue(value: unknown): value is AssistValue {
  return value === null || typeof value === "string" ||
    (typeof value === "number" && Number.isSafeInteger(value)) ||
    (Array.isArray(value) && value.every((item) => typeof item === "string"));
}

function validDerivedDates(value: unknown, max: number): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.length <= max &&
    value.every((day) => typeof day === "string" && isIsoDate(day)) &&
    new Set(value).size === value.length;
}

/** 서버가 검증해 돌려준 날짜 목록의 유도 근거만 다음 턴에 전달해요. */
function derivedListMetadata(
  form: AssistForm, field: string, raw: Record<string, unknown>, opts?: RuleOptions,
): Pick<FieldIntent, "intent_kind" | "value_source" | "correction"> {
  // A computed scalar has its own baseline/operation proof. Preserve only
  // intact metadata; do not preserve a scalar correction as a list relation.
  if ((form === "quote" && field === "days" || form === "dates" && field === "crew") &&
    raw.action === "set" && raw.status === "valid" && raw.value_source === "derived" && raw.correction == null &&
    (raw.intent_kind === "explicit_change" || raw.intent_kind === "retain_previous") &&
    typeof raw.value === "number" && Number.isSafeInteger(raw.value) && raw.value > 0 &&
    typeof raw.evidence === "string" && !!raw.evidence.trim() && raw.evidence.length <= 1000 &&
    (!opts || validSetValue(form,field,raw.value,opts))) {
    return {intent_kind:raw.intent_kind,value_source:"derived"};
  }
  const max = form === "dates" ? 120 : 31;
  if (!((form === "dates" && field === "days") || (form === "period" && field === "excluded")) ||
    raw.action !== "set" || raw.status !== "valid" || raw.value_source !== "derived" ||
    typeof raw.evidence !== "string" || !raw.evidence.trim() || raw.evidence.length > 1000 ||
    !validDerivedDates(raw.value, max) ||
    (opts && !validSetValue(form, field, raw.value, opts))) return {};

  const result: Pick<FieldIntent, "intent_kind" | "value_source" | "correction"> = {
    value_source: "derived",
  };
  if (raw.intent_kind === "explicit_change") result.intent_kind = "explicit_change";
  const candidate = raw.correction;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return result;
  const link = candidate as Record<string, unknown>;
  const rejected = link.rejected_value;
  const rejectedValue: AssistValue | undefined = typeof rejected === "string" && isIsoDate(rejected)
    ? rejected : validDerivedDates(rejected, max) ? [...rejected] : undefined;
  if (link.kind !== "replace" || rejectedValue === undefined ||
    typeof link.rejected_evidence !== "string" || !link.rejected_evidence.trim() ||
    link.rejected_evidence.length > 1000 ||
    typeof link.accepted_evidence !== "string" || !link.accepted_evidence.trim() ||
    link.accepted_evidence.length > 1000) return result;
  result.correction = {
    kind: "replace",
    rejected_value: rejectedValue,
    rejected_evidence: link.rejected_evidence,
    accepted_evidence: link.accepted_evidence,
  };
  return result;
}

function candidateFor(field: string, raw: unknown, form: AssistForm, opts: RuleOptions): CandidateField {
  const data = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const action = ["keep", "set", "clear", "uncertain"].includes(String(data.action)) ? data.action as AssistAction : "uncertain";
  const status = ["valid", "conflict", "invalid", "uncertain"].includes(String(data.status))
    ? data.status as CandidateField["status"] : "invalid";
  const source: AssistSource = data.source === "llm" ? "llm" : "rule";
  const reasonCode = typeof data.reason_code === "string" &&
    ["evidence_missing", "unsupported_normalization", "type_mismatch", "business_constraint",
      "stale_context", "ambiguous_intent", "combined_constraint", "reference_missing", "correction_unverified"].includes(data.reason_code)
    ? data.reason_code as NonNullable<CandidateField["reason_code"]> : undefined;
  const origin = typeof data.origin_request_id === "string" && data.origin_request_id.length <= 80
    ? data.origin_request_id : null;
  const evidence = typeof data.evidence === "string" ? data.evidence.slice(0, 1000) : "";
  const value = isValue(data.value) ? data.value : null;
  const alternatives: Alternative[] = Array.isArray(data.alternatives)
    ? data.alternatives.flatMap((item: unknown): Alternative[] => {
      if (!item || typeof item !== "object") return [];
      const alt = item as Record<string, unknown>;
      if ((alt.action !== "set" && alt.action !== "clear") || typeof alt.evidence !== "string" ||
        !alt.evidence.trim() || (alt.source !== "rule" && alt.source !== "llm") ||
        (alt.action === "set" && !validSetValue(form, field, alt.value, opts)) ||
        (alt.action === "clear" && (alt.value !== null || opts.immutableFields?.includes(field)))) return [];
      return [{
        action: alt.action as "set" | "clear",
        value: alt.action === "clear" ? null : alt.value as AssistValue,
        evidence: alt.evidence.slice(0, 1000),
        source: alt.source === "llm" ? "llm" as const : "rule" as const,
      }];
    }).filter((item, index, all) => all.findIndex(other =>
      other.action === item.action && sameValue(other.value, item.value)) === index).slice(0, 8) : [];
  if (status === "conflict") {
    return { field, action: "uncertain", value: null, evidence, source, status: alternatives.length >= 2 ? "conflict" : "uncertain", alternatives: alternatives.length >= 2 ? alternatives : [], origin_request_id: origin, ...(reasonCode ? { reason_code: reasonCode } : {}) };
  }
  const validAction = action === "set" ? validSetValue(form, field, value, opts) :
    action !== "uncertain" && value === null && (action !== "clear" || !opts.immutableFields?.includes(field));
  const safeStatus = validAction && status === "valid" ? "valid" : status === "uncertain" ? "uncertain" : "invalid";
  const metadata: Partial<FieldIntent> = safeStatus === "valid" && action === "set"
    ? derivedListMetadata(form, field, data, opts)
    : safeStatus === "valid" && action === "keep" && evidence.trim() &&
      data.intent_kind === "retain_current" && data.value_source === "current"
      ? { intent_kind: "retain_current", value_source: "current" } : {};
  if (safeStatus === "valid" && action === "keep" && !evidence.trim() && data.intent_kind === "no_mention") {
    metadata.intent_kind = "no_mention";
  }
  if (safeStatus === "valid" && (action === "set" || action === "clear") && data.intent_kind === "retain_previous") {
    metadata.intent_kind = "retain_previous";
    if (data.value_source === "previous") metadata.value_source = "previous";
  }
  return { field, action, value: validAction ? value : null, evidence, source, status: safeStatus, alternatives: [], origin_request_id: origin, ...metadata, ...(safeStatus !== "valid" && reasonCode ? { reason_code: reasonCode } : {}) };
}

// 외부 응답의 형태·필드·값을 재확인하고 중복이나 알 수 없는 필드를 화면에서 버려요.
export function readResponse(raw: unknown, request: AssistRequest, opts: RuleOptions): AssistResponse {
  if (!raw || typeof raw !== "object") throw new Error("해석 결과 형식이 올바르지 않아요.");
  const data = raw as Record<string, unknown>;
  if (data.request_id !== request.request_id || data.form !== request.form ||
    !Array.isArray(data.fields) || (data.source !== "rule" && data.source !== "llm")) {
    throw new Error("해석 결과가 요청과 맞지 않아요.");
  }
  const rawFields: unknown[] = data.fields;
  const fields = formKeys(request.form).map((field) => {
    const matches = rawFields.filter((item: unknown) => item && typeof item === "object" &&
      (item as Record<string, unknown>).field === field);
    return matches.length === 1 ? candidateFor(field, matches[0], request.form, opts) : emptyCandidate(field);
  });
  return {
    request_id: request.request_id,
    form: request.form,
    source: data.source,
    fields,
    warnings: Array.isArray(data.warnings)
      ? data.warnings.filter((warning: unknown) => typeof warning === "string").slice(0, 10).map((warning: string) => warning.slice(0, 300))
      : [],
  };
}

export function mergeKeptFields(previous: AssistResponse | null, latest: AssistResponse): AssistResponse {
  if (!previous || previous.form !== latest.form) return latest;
  const fields = latest.fields.map((field) => {
    if (field.action !== "keep") return field;
    const old = previous.fields.find((item) => item.field === field.field);
    // 규칙 전용 응답에는 origin_request_id가 없을 수 있어요. 최초 요청 ID를 붙여 계승해요.
    return old?.status === "valid" && (old.action === "set" || old.action === "clear")
      ? { ...old, origin_request_id: old.origin_request_id ?? previous.request_id }
      : field;
  });
  return { ...latest, fields };
}

/** 현재 폼과 후보 생성 당시 값이 같은 미확정 칸만 다음 요청으로 이어요. */
export function freshPrevious(
  response: AssistResponse | null, baselines: AssistCurrent | null, current: AssistCurrent,
): AssistResponse | null {
  if (!response || !baselines) return null;
  const fields = response.fields.filter((field) =>
    field.status === "valid" && (field.action === "set" || field.action === "clear") &&
    Object.prototype.hasOwnProperty.call(baselines, field.field) &&
    sameValue(baselines[field.field], current[field.field]));
  return fields.length ? { ...response, fields } : null;
}

/** 변경 후보의 원래 Send 기준값·출처를 값마다 보존해요. */
export function previousCandidate(
  response: AssistResponse, text: string, baselines: AssistCurrent,
): PreviousCandidate {
  const fields = response.fields.filter((field) =>
    field.status === "valid" && (field.action === "set" || field.action === "clear") &&
    Object.prototype.hasOwnProperty.call(baselines, field.field));
  return {
    request_id: response.request_id,
    text,
    sources: Object.fromEntries(fields.map((field) => [field.field, field.source])),
    origins: Object.fromEntries(fields.map((field) => [field.field, field.origin_request_id ?? response.request_id])),
    baselines: Object.fromEntries(fields.map((field) => {
      const value = baselines[field.field];
      return [field.field, Array.isArray(value) ? [...value] : value];
    })),
    fields: fields.map((field) => ({
      field: field.field,
      action: field.action,
      value: Array.isArray(field.value) ? [...field.value] : field.value,
      evidence: field.evidence,
      ...derivedListMetadata(response.form, field.field, field as unknown as Record<string, unknown>),
    })),
  };
}

/** 부분 적용도 최종 달력 상태가 유효해야 해요. 고른 필드만 기존 상태와 합쳐 검사해요. */
export function validCombinedPeriod(current: AssistCurrent, selected: AssistCurrent, opts: RuleOptions): boolean {
  const start = Object.prototype.hasOwnProperty.call(selected, "start") ? selected.start : current.start;
  const end = Object.prototype.hasOwnProperty.call(selected, "end") ? selected.end : current.end;
  const rawExcluded = Object.prototype.hasOwnProperty.call(selected, "excluded") ? selected.excluded : current.excluded;
  const excluded = rawExcluded === null ? [] : rawExcluded;
  if (start !== null && !validSetValue("period", "start", start, opts)) return false;
  if (end !== null && !validSetValue("period", "end", end, opts)) return false;
  if (start === null && end !== null) return false;
  if (typeof start === "string" && typeof end === "string" &&
      (start > end || Date.parse(end) - Date.parse(start) > 62 * 86_400_000)) return false;
  if (!Array.isArray(excluded) || excluded.some((date) => typeof date !== "string")) return false;
  if (excluded.length && (start === null || end === null)) return false;
  if (excluded.length && !validSetValue("period", "excluded", excluded, opts)) return false;
  if (excluded.some((date) => date <= start! || date >= end!)) return false;
  return true;
}

/** Raw wire identity is necessary but never a semantic authorization. */
export type WireOperation =
  | { kind: "explicit_change"; valueSource: "utterance" | "derived" }
  | { kind: "retain_current"; valueSource: "current" }
  | { kind: "retain_previous"; valueSource: "previous" | "derived" }
  | { kind: "no_mention"; valueSource: null };

export function normalizedWireEvidence(text: string): string {
  return text.normalize("NFKC").replace(/\s+/g, "");
}

export function uniqueWireField(raw: unknown, field: string): Record<string, unknown> | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
  const fields = (raw as Record<string, unknown>).fields;
  if (!Array.isArray(fields)) return;
  let found: Record<string, unknown> | undefined;
  for (const item of fields) {
    if (!item || typeof item !== "object" || Array.isArray(item) || item.field !== field) continue;
    if (found) return;
    found = item;
  }
  return found;
}

function matchesWireRelation(raw: unknown, expected: FieldIntent["correction"], text: string): boolean {
  if (raw == null || expected == null) return raw == null && expected == null;
  if (typeof raw !== "object" || Array.isArray(raw)) return false;
  const relation = raw as Record<string, unknown>;
  if (relation.kind !== "replace" || expected.kind !== "replace" ||
      !isValue(relation.rejected_value) || !sameValue(relation.rejected_value, expected.rejected_value)) return false;
  const original = normalizedWireEvidence(text);
  return (["rejected_evidence", "accepted_evidence"] as const).every(key => {
    const value = relation[key];
    return typeof value === "string" && value.length <= 1000 &&
      !!normalizedWireEvidence(value) && original.includes(normalizedWireEvidence(value)) &&
      normalizedWireEvidence(value) === normalizedWireEvidence(expected[key]);
  });
}

/**
 * Classify only intact server metadata for one field. The original response
 * and parsed candidate must agree; normalization cannot repair a bad wire.
 * Value semantics, field ownership, independent risk and application guards
 * are handled by the caller. Legacy or unverified metadata stays on the
 * conservative path. No wire field alone can resolve a real obligation.
 */
export function wireOperation(raw: unknown, request: AssistRequest, field: CandidateField): WireOperation | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const envelope = raw as Record<string, unknown>;
  if (envelope.request_id !== request.request_id || envelope.form !== request.form ||
      (envelope.source !== "llm" && envelope.source !== "rule") ||
      !formKeys(request.form).includes(field.field)) return null;
  const item = uniqueWireField(raw, field.field);
  if (!item || field.status !== "valid" || item.status !== "valid" ||
      item.action !== field.action || (item.source !== "llm" && item.source !== "rule") ||
      item.source !== field.source || !isValue(item.value) || !sameValue(item.value, field.value) ||
      typeof item.evidence !== "string" || item.evidence.length > 1000 || item.evidence !== field.evidence ||
      typeof item.origin_request_id !== "string" || !item.origin_request_id ||
      item.origin_request_id.length > 80 || item.origin_request_id !== field.origin_request_id ||
      !Array.isArray(item.alternatives) || item.alternatives.length || field.alternatives.length) return null;
  const evidence = normalizedWireEvidence(item.evidence);
  const fresh = item.origin_request_id === request.request_id;
  const actionable = field.action === "set" && field.value !== null ||
    field.action === "clear" && field.value === null;
  if (item.intent_kind === "no_mention" && fresh && field.action === "keep" &&
      field.value === null && !evidence && item.value_source == null && item.correction == null)
    return { kind: "no_mention", valueSource: null };
  if (item.intent_kind === "retain_current" && fresh && field.action === "keep" &&
      field.value === null && evidence && item.value_source === "current" && item.correction == null &&
      normalizedWireEvidence(request.text).includes(evidence))
    return { kind: "retain_current", valueSource: "current" };
  if (item.intent_kind === "explicit_change" && fresh && actionable && evidence &&
      (item.value_source === "utterance" || item.value_source === "derived" && field.action === "set") &&
      normalizedWireEvidence(request.text).includes(evidence) &&
      // Corrections require their separate complete relationship proof.
      matchesWireRelation(item.correction, field.correction, request.text))
    return { kind: "explicit_change", valueSource: item.value_source };
  if (item.intent_kind !== "retain_previous" || fresh || !actionable || !evidence ||
      (item.value_source !== "previous" && item.value_source !== "derived")) return null;
  const prior = request.previous_candidate;
  const old = prior?.fields.find(value => value.field === field.field);
  if (!prior || !old || old.action !== field.action || !sameValue(old.value, field.value) ||
      normalizedWireEvidence(old.evidence) !== evidence || !normalizedWireEvidence(prior.text).includes(evidence) ||
      (prior.origins?.[field.field] ?? prior.request_id) !== item.origin_request_id ||
      prior.sources?.[field.field] !== item.source ||
      !Object.prototype.hasOwnProperty.call(prior.baselines ?? {}, field.field) ||
      !sameValue(prior.baselines?.[field.field] ?? null, request.current[field.field] ?? null) ||
      (old.value_source === "derived") !== (item.value_source === "derived") ||
      !matchesWireRelation(item.correction, old.correction, prior.text) ||
      !matchesWireRelation(item.correction, field.correction, prior.text)) return null;
  return { kind: "retain_previous", valueSource: item.value_source };
}
