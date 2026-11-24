// 기존 규칙 파서는 서버 응답과 같은 후보 모양으로 바꿔 미리보기에만 사용해요.
import { parseDates, parseExtra, parsePeriod, parseQuote } from "./parse";
import { requestIntent } from "./requestIntent";
import {
  type AssistForm, type AssistResponse, type AssistValue, type FieldIntent,
  type RuleOptions, formKeys, keepField, validSetValue,
} from "./protocol";

export function ruleFields(form: AssistForm, text: string, opts: RuleOptions): FieldIntent[] {
  const found: Record<string, AssistValue> = {};
  if (form === "period") {
    const result = parsePeriod(text, opts.today, opts.baseMonth);
    if (result) {
      found.start = result.start;
      found.end = result.end;
      // 빼는 날을 말하지 않았다고 기존 제외일을 자동 삭제하지 않아요.
      if (result.excluded.length) found.excluded = result.excluded;
    }
  } else if (form === "quote") {
    Object.assign(found, parseQuote(text, opts.today));
  } else if (form === "dates") {
    const result = parseDates(text, opts.today, opts.baseMonth, opts.holidays, opts.noWeekend);
    if (result?.days.length) found.days = result.days;
    if (result?.crew !== undefined) found.crew = result.crew;
  } else {
    Object.assign(found, parseExtra(text, opts.today));
  }
  const intent = requestIntent(form, text);
  return formKeys(form).map((field) => {
    const value = found[field];
    return value === undefined || intent.blocked.has(field) || intent.serverOnly.has(field) || intent.cancelAll
      ? keepField(field) : { field, action: "set", value, evidence: text };
  });
}

export function ruleResponse(
  form: AssistForm, requestId: string, text: string, opts: RuleOptions, fields = ruleFields(form, text, opts),
): AssistResponse {
  const warnings: string[] = [];
  if (form === "period") {
    const parsed = parsePeriod(text, opts.today, opts.baseMonth);
    if (parsed?.outside.length) warnings.push("기간 밖의 제외일은 후보에 포함하지 않았어요: " + parsed.outside.join(", "));
  }
  return {
    form,
    request_id: requestId,
    source: "rule",
    warnings,
    fields: fields.map((item) => ({
      ...item,
      source: "rule" as const,
      status: item.action === "keep" ? "valid" as const :
        validSetValue(form, item.field, item.value, opts) ? "valid" as const : "invalid" as const,
      alternatives: [],
    })),
  };
}
