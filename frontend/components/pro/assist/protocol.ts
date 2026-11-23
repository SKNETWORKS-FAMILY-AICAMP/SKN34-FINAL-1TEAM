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
    if (!Array.isArray(value) || value.length > 62 || !value.every((day) => typeof day === "string" && isIsoDate(day))) return false;
    if (new Set(value).size !== value.length) return false;
    if (form === "dates") {
      if (!value.length || value.some((day) => day <= opts.today || opts.holidays?.includes(day))) return false;
      if (opts.noWeekend && value.some((day) => [0, 6].includes(new Date(day + "T00:00:00Z").getUTCDay()))) return false;
      if (opts.requiredDays && value.length !== opts.requiredDays) return false;
    }
    return true;
  }
  if (field === "amount") {
    return typeof value === "number" && Number.isSafeInteger(value) && value > 0 &&
      value <= 1_000_000_000 && (form !== "extra" || String(value).length <= 9);
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
    return typeof value === "string" && isIsoDate(value) && value >= opts.today;
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

function candidateFor(field: string, raw: unknown, form: AssistForm, opts: RuleOptions): CandidateField {
  const data = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const action = ["keep", "set", "clear", "uncertain"].includes(String(data.action)) ? data.action as AssistAction : "uncertain";
  const status = ["valid", "conflict", "invalid", "uncertain"].includes(String(data.status))
    ? data.status as CandidateField["status"] : "invalid";
  const source: AssistSource = data.source === "llm" ? "llm" : "rule";
  const origin = typeof data.origin_request_id === "string" && data.origin_request_id.length <= 80
    ? data.origin_request_id : null;
  const evidence = typeof data.evidence === "string" ? data.evidence.slice(0, 1000) : "";
  const value = isValue(data.value) ? data.value : null;
  const alternatives: Alternative[] = Array.isArray(data.alternatives)
    ? data.alternatives.flatMap((item: unknown): Alternative[] => {
      if (!item || typeof item !== "object") return [];
      const alt = item as Record<string, unknown>;
      if ((alt.action !== "set" && alt.action !== "clear") || typeof alt.evidence !== "string" ||
        (alt.action === "set" && !validSetValue(form, field, alt.value, opts)) ||
        (alt.action === "clear" && (alt.value !== null || opts.immutableFields?.includes(field)))) return [];
      return [{
        action: alt.action as "set" | "clear",
        value: alt.action === "clear" ? null : alt.value as AssistValue,
        evidence: alt.evidence.slice(0, 1000),
        source: alt.source === "llm" ? "llm" as const : "rule" as const,
      }];
    }).slice(0, 8) : [];
  if (status === "conflict") {
    return { field, action: "uncertain", value: null, evidence, source, status: alternatives.length ? "conflict" : "uncertain", alternatives, origin_request_id: origin };
  }
  const validAction = action === "set" ? validSetValue(form, field, value, opts) :
    value === null && (action !== "clear" || !opts.immutableFields?.includes(field));
  const safeStatus = validAction && status === "valid" ? "valid" : status === "uncertain" ? "uncertain" : "invalid";
  return { field, action, value: validAction ? value : null, evidence, source, status: safeStatus, alternatives: [], origin_request_id: origin };
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
    return old?.status === "valid" && (old.action === "set" || old.action === "clear") ? old : field;
  });
  return { ...latest, fields };
}

export function previousCandidate(response: AssistResponse, text: string): PreviousCandidate {
  return {
    request_id: response.request_id,
    text,
    sources: Object.fromEntries(response.fields.map((field) => [field.field, field.source])),
    origins: Object.fromEntries(response.fields.map((field) => [field.field, field.origin_request_id ?? response.request_id])),
    fields: response.fields.map((field) =>
      field.status === "valid" && (field.action === "set" || field.action === "clear")
        ? { field: field.field, action: field.action, value: field.value, evidence: field.evidence }
        : keepField(field.field)),
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
  if (start !== null && end !== null && start > end) return false;
  if (!Array.isArray(excluded) || excluded.some((date) => typeof date !== "string")) return false;
  if (excluded.length && (start === null || end === null)) return false;
  if (excluded.length && !validSetValue("period", "excluded", excluded, opts)) return false;
  if (excluded.some((date) => date <= start! || date >= end!)) return false;
  return true;
}
