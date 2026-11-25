// 기존 규칙 파서는 서버 응답과 같은 후보 모양으로 바꿔 미리보기에만 사용해요.
import { extraTextScopes, independentPeriodBoundary, independentPeriodExclusionBoundary, independentPeriodInquiry, parseDates, parseExtra, parsePeriod, parseQuote, readDateListQuantity, readExplicitPeriodBoundary, readExplicitPeriodExclusions } from "./parse";
import { requestIntent } from "./requestIntent";
import {
  type AssistForm, type AssistResponse, type AssistValue, type FieldIntent,
  type RuleOptions, formKeys, keepField, validSetValue,
} from "./protocol";

export function ruleFields(form: AssistForm, text: string, opts: RuleOptions): FieldIntent[] {
  const found: Record<string, AssistValue> = {};
  const evidence: Record<string, string> = {};
  const intent = requestIntent(form, form === "extra" ? extraTextScopes(text).intentText : text);
  if (form === "period") {
    const result = parsePeriod(text, opts.today, opts.baseMonth);
    if (result) {
      if (intent.requested.has("start")) found.start = result.start;
      if (intent.requested.has("end")) found.end = result.end;
      // 빼는 날을 말하지 않았다고 기존 제외일을 자동 삭제하지 않아요.
      if (result.excluded.length) found.excluded = result.excluded;
    } else {
      const independent = independentPeriodBoundary(text, opts.today, opts.baseMonth) ??
        independentPeriodInquiry(text, opts.today, opts.baseMonth) ??
        independentPeriodExclusionBoundary(text, opts.today, opts.baseMonth);
      if (independent) {
        found[independent.field] = independent.value;
        evidence[independent.field] = independent.evidence;
      }
    }
    for(const field of ["start","end"] as const){
      const proof=readExplicitPeriodBoundary(text,field,opts.today,opts.baseMonth);
      if(proof?.ambiguous)delete found[field];
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
  if (form === "dates" || form === "period") {
    const proof = readDateListQuantity(form,text,opts.today,opts.baseMonth,opts.holidays,opts.noWeekend) ??
      (form==="period"&&!found.excluded?readExplicitPeriodExclusions(text,opts.today,opts.baseMonth):undefined);
    const field = form === "dates" ? "days" : "excluded";
    if (proof) {
      if (proof.invalid) delete found[field];
      else if (!intent.blocked.has(field)) found[field] = proof.dates;
    }
  }
  return formKeys(form).map((field) => {
    const value = found[field];
    const withdrawal=intent.withdrawalProofs.get(field);
    const keep=intent.linkedCurrentKeep.get(field) ??
      (withdrawal&&!withdrawal.valueText&&!withdrawal.selectedMode&&withdrawal.reference!=="previous"?withdrawal.scope:undefined);
    if(keep)return {...keepField(field),evidence:keep,intent_kind:"retain_current",value_source:"current"};
    return value === undefined || intent.blocked.has(field) || intent.serverOnly.has(field) || intent.cancelAll
      ? keepField(field) : { field, action: "set", value, evidence: evidence[field] ?? text };
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
      ...(item.intent_kind==="retain_current"&&item.value_source==="current"?{origin_request_id:requestId}:{}),
      status: item.action === "keep" ? "valid" as const :
        validSetValue(form, item.field, item.value, opts) ? "valid" as const : "invalid" as const,
      alternatives: [],
    })),
  };
}
