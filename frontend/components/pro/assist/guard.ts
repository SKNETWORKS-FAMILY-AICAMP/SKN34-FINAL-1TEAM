import { parseDates, parsePeriod, parseQuote, readCrew, recognizedCompoundAmount, SAID_AMOUNT, uncertainCompoundAmount } from "./parse";
import { validSetValue, type AssistForm, type RuleOptions } from "./protocol";
import { periodIssueScope, type PeriodIssueScope } from "./periodHold";
import { writtenMoney } from "./writtenMoney";
import { requestIntent } from "./requestIntent";

// 문장 사이의 옛 금액 오류 인정과 새 금액 확정을 함께 읽어요. 서버의 정정 검증은 별도로 거쳐요.
const REPORTED_AMOUNT_CLAUSE = /(?:이라고|라고|이라|라)\s*(?:제가\s*)?(?:(?:잘못|틀리게)\s*)?(?:말씀드렸|말했|전달했|적었|입력했|썼)[^.!?？。]*[.!?？。]/;
const REPORT_MONEY = String.raw`(?:(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,4})?)\s*만(?:\s*[1-9]\s*천)?(?:\s*원)?|(?:\d{1,3}(?:,\d{3})+|\d{6,})\s*원)`;
const REPORT_OLD = new RegExp(String.raw`^(?:(?:아까|앞서|방금|이전에)\s*)?(?:제가\s*)?(?:총액|합계|견적\s*금액|견적|금액|공사비)\s*(?:[은는이가을를:]\s*)?(${REPORT_MONEY})\s*(?:이라고|라고|이라|라)\s*(?:제가\s*)?(?:잘못|틀리게)\s*(?:말씀드렸|말했|전달했|적었|입력했|썼)(?:네요|어요|습니다|죠)$`);
const REPORT_NEW = new RegExp(String.raw`^(?:정확히는|정확한\s*금액은|실제\s*금액은)\s*(${REPORT_MONEY})\s*(?:이에요|예요|입니다|이\s*맞아요|이\s*맞습니다|맞아요|맞습니다)$`);
const REPORT_CONFIRM = /^(?:그|이)\s*금액(?:으로|로)\s*(?:정정\s*부탁해요|정정\s*부탁드립니다|정정\s*해\s*주세요|정정할게요|정정하겠습니다|수정\s*해\s*주세요|고쳐\s*주세요)$/;

function reportedAmountError(text: string): boolean {
  const report = REPORTED_AMOUNT_CLAUSE.exec(text);
  return !!report && SAID_AMOUNT.test(text.slice(0, report.index)) &&
    (/잘못|틀리게/.test(report[0]) || /정확히는|정확한\s*금액|실제\s*금액|정정/.test(text.slice(report.index + report[0].length)));
}

function reportedAmountToConfirm(text: string, today: string): number | undefined {
  if (/[?？]/.test(text)) return undefined;
  const clauses = text.trim().replace(/[.!。]$/, "").split(/(?<!\d)\.|\.(?!\d)|[!。]/).map((s) => s.trim());
  if (clauses.length !== 3 || !REPORT_CONFIRM.test(clauses[2])) return undefined;
  const oldMatch = clauses[0].match(REPORT_OLD);
  const newMatch = clauses[1].match(REPORT_NEW);
  if (!oldMatch || !newMatch) return undefined;
  const old = parseQuote(oldMatch[1], today).amount;
  const next = parseQuote(newMatch[1], today).amount;
  return old !== undefined && old <= 1_000_000_000 && next !== old ? next : undefined;
}

const HISTORICAL_AMOUNT = /(?<![\d천백십만억])(?<!\d[,.])(?:(?:(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,4})?|(?:\d+\s*[천백십]\s*)+\d*)\s*만(?:\s*[1-9]\s*천|\s*(?:\d{1,3}(?:,\d{3})+|\d+)\s*원)?\s*원?)|(?:\d{1,3}(?:,\d{3})+|\d{6,})\s*원)/g;
const HISTORICAL_REPORT = /(?:이라고|라고|이라|라|으로|로)(?:제가)?(?:(?:했|말했|말씀드렸|적었|썼|입력했|전달했|기재했)(?:는데|다가|지만|고)|한|했던|말한|말했던|말씀드린|적은|입력한|던)/;
const REPORT_VERB = "(?:했|말했|말씀드렸|적었|썼|입력했|전달했|기재했)";
const REPORT_ERROR = "(?:제가)?(?:(?:계산|합계|금액|금액계산|견적|숫자|단가)(?:을|를)?)?(?:잘못|틀리게)(?:했|봤|보았|계산했|말했|말씀드렸|적었|썼|입력했|전달했|기재했)(?:네요|어요|습니다|죠)[.!。]";
const HISTORICAL_ACTION = /^(?:으로|로)(?:(?:정정|수정|변경|확정)(?:부탁해요|부탁드립니다|해주세요|해줘요|해주십시오|해요|할게요|하겠습니다|합니다)|(?:고쳐|바꿔)(?:주세요|줘요|주십시오|요)|(?:고칠|바꿀|올릴|내릴)게요|(?:고치|바꾸|올리|내리)겠습니다|(?:고칩|바꿉)니다)$/;
const CORRECTION_RISK = /가능하면|다면|라면|으면|이면|하면|경우|일때|취소|철회|보류|미정|없(?:던|었던)(?:걸로|일로|것으로)|원래대로|그대로두|하지마|하지않|안할|아니|아마|혹시|아직|검토|예정|잠정|고민|또는|혹은|수도|지도|것같|(?:할|일|될)까|정도|쯤|[?？]/;
const AMOUNT_LABEL = /금액|추가금|견적|공사비|총액|합계/;
const OTHER_FIELD_LABEL = /세금|부가세|사유|이유|제목|항목|인원|기간|일수|일정/;

export function historicalAmountReport(text: string): boolean {
  const compact = text.normalize("NFKC").replace(/\s+/g, "");
  const report = HISTORICAL_REPORT.exec(compact);
  return !!report && SAID_AMOUNT.test(compact.slice(0, report.index));
}

/** 보고된 옛 값과 새 값을 모두 읽되, 이전 후보 확인은 세션에서 해요. */
export function historicalAmountCorrection(
  text: string, today: string, form: AssistForm,
): { old: number; next: number } | undefined {
  if (form !== "quote" && form !== "extra") return undefined;
  const raw = text.normalize("NFKC");
  const compact = (value: string) => value.replace(/\s+/g, "");
  const amounts = [...raw.matchAll(HISTORICAL_AMOUNT)];
  if (amounts.length !== 2) return undefined;
  const [oldMatch, newMatch] = amounts;
  const prefix = compact(raw.slice(0, oldMatch.index));
  const middle = compact(raw.slice(oldMatch.index + oldMatch[0].length, newMatch.index));
  const tail = compact(raw.slice(newMatch.index + newMatch[0].length));
  if ([prefix, middle, tail].some((part) => SAID_AMOUNT.test(part))) return undefined;
  const label = form === "quote"
    ? "(?:견적(?:가|금액)?|총액|합계|금액|공사비|비용)"
    : "(?:추가(?:공사)?(?:금액|금|비용|비)|금액|합계|비용)";
  const prefixParts = prefix.split(/[,;.!?？。]/);
  const target = prefixParts.pop() ?? "";
  const targetPattern = new RegExp("^(?:(?:아까|앞서|지난번(?:에)?|이전에|예전에|방금|조금전)(?:에)?)?(?:제가)?(?:" + label + "(?:은|는|이|가|을|를|:)?)?$");
  if (!targetPattern.test(target)) return undefined;
  const reportLink = new RegExp("^(?:(?:이라고|라고|이라|라|으로|로)?(?:제가)?" + REPORT_VERB +
    "(?:는데|다가|지만|고)(?:" + REPORT_ERROR + ")?|(?:이라고|라고|이라|라)(?:한|했던|말한|말했던|말씀드린|적은|입력한|던)" +
    label + "(?:만|은|는|을|를|이|가)?)$");
  if (!reportLink.test(middle)) return undefined;
  const tailParts = tail.split(/([,;.!?？。])/);
  if (tailParts[1] === "?" || tailParts[1] === "？" || !HISTORICAL_ACTION.test(tailParts[0])) return undefined;
  const rest = [...prefixParts, ...tailParts.slice(2)];
  if (rest.some((part) => CORRECTION_RISK.test(part) &&
    (AMOUNT_LABEL.test(part) || !OTHER_FIELD_LABEL.test(part)))) return undefined;
  const old = parseQuote(oldMatch[0], today).amount;
  const next = parseQuote(newMatch[0], today).amount;
  if (old === undefined || next === undefined || old === next || old > 1_000_000_000 || next > 1_000_000_000) return undefined;
  return { old, next };
}

/** 규칙이 한 금액으로 축약하지 못해도 명시적으로 새로 확정한 금액은 서버 후보와 대조해요. */
export function amountToConfirm(text: string, today: string, form: AssistForm = "quote"): number | undefined {
  const written = writtenMoney(text);
  if (written.mentioned) return written.amount;
  if (historicalAmountReport(text)) return historicalAmountCorrection(text, today, form)?.next;
  if (form === "quote" && reportedAmountError(text)) return reportedAmountToConfirm(text, today);
  if (uncertainCompoundAmount(text)) return undefined;
  const compound = recognizedCompoundAmount(text);
  if (compound !== undefined) return compound;
  const parts = text.split(/잘못\s*(?:적었고|썼고|입력했고)|(?:이?라고|이라)\s*(?:했는데|말했는데)/);
  if (parts.length !== 2 || !SAID_AMOUNT.test(parts[0])) return undefined;
  const [clause, ...later] = parts[1].trim().split(/[.!?？]/);
  const change = clause.match(/^(.{1,45}?)(?:으로|로)\s*정정(?:해\s*주세요|할게요|하겠습니다)$/);
  if (!change || /[?？]|취소|철회|보류|미정/.test(text) || later.some((s) => SAID_AMOUNT.test(s))) return undefined;
  return parseQuote(change[1], today).amount;
}

type Issues = Partial<Record<"amount" | "crew" | "days" | "period", string>> & {
  periodScope?: PeriodIssueScope;
  fieldHolds?: Partial<Record<string, string | null>>;
  pending?: boolean;
};
const DATE_WORD = /\d\s*(?:일|월|\/)|\d{4}[-.]\d{1,2}[-.]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|요일/;

export function assistInputIssues(form: AssistForm, text: string, opts: RuleOptions, confirmedAmount?: number): Issues {
  const issues: Issues = {};
  const intent = requestIntent(form, text);
  if (form === "quote" || form === "extra") {
    const written = writtenMoney(text);
    const amount = confirmedAmount ?? parseQuote(text, opts.today).amount;
    if (amount !== undefined && amount > 1_000_000_000) issues.amount = "10억 원까지 넣을 수 있어요.";
    else if (intent.blocked.has("amount") || (written.mentioned && (written.amount === undefined || written.amount !== confirmedAmount)) ||
      uncertainCompoundAmount(text) ||
      (historicalAmountReport(text) && confirmedAmount === undefined) ||
      (form === "quote" && reportedAmountError(text) && confirmedAmount === undefined) ||
      (amount === undefined && SAID_AMOUNT.test(text))) {
      issues.amount = "말씀하신 금액을 확인하지 못했어요. 금액을 직접 적어 주세요.";
    }
  }
  if (form === "dates") {
    const parsed = parseDates(text, opts.today, opts.baseMonth, opts.holidays, opts.noWeekend);
    const crew = readCrew(text);
    if (intent.blocked.has("crew") || crew.mentioned && (!crew.value || crew.value > (opts.crewMax ?? 6))) {
      issues.crew = "말씀하신 인원을 확정할 수 없어요. 보낼 인원을 다시 골라 주세요.";
    }
    if (DATE_WORD.test(crew.withoutCrew) && (!parsed?.days.length || !validSetValue("dates", "days", parsed.days, opts) ||
      intent.blocked.has("days") || intent.replacements.has("days"))) {
      issues.days = "말씀하신 날짜를 넣지 못했어요. 달력에서 공사할 날을 다시 골라 주세요.";
    }
  }
  if (form === "period" && DATE_WORD.test(text)) {
    const parsed = parsePeriod(text, opts.today, opts.baseMonth);
    if (!parsed || parsed.start <= opts.today || intent.blocked.has("start") || intent.blocked.has("end") ||
        intent.blocked.has("excluded") || intent.replacements.has("excluded")) {
      issues.period = "말씀하신 기간을 넣지 못했어요. 달력에서 기간을 다시 눌러 주세요.";
      issues.periodScope = periodIssueScope(text);
    }
  }
  return issues;
}
