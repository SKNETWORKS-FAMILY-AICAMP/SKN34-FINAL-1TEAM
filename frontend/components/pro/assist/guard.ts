import { parseDates, parsePeriod, parseQuote, readCrew, recognizedCompoundAmount, SAID_AMOUNT, uncertainCompoundAmount } from "./parse";
import type { AssistForm, RuleOptions } from "./protocol";

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

/** 규칙이 한 금액으로 축약하지 못해도 명시적으로 새로 확정한 금액은 서버 후보와 대조해요. */
export function amountToConfirm(text: string, today: string, form: AssistForm = "quote"): number | undefined {
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

type Issues = Partial<Record<"amount" | "crew" | "days" | "period", string>>;
const DATE_WORD = /\d\s*(?:일|월|\/)|\d{4}[-.]\d{1,2}[-.]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|요일/;

export function assistInputIssues(form: AssistForm, text: string, opts: RuleOptions, confirmedAmount?: number): Issues {
  const issues: Issues = {};
  if (form === "quote" || form === "extra") {
    const amount = confirmedAmount ?? parseQuote(text, opts.today).amount;
    if (amount !== undefined && amount > 1_000_000_000) issues.amount = "10억 원까지 넣을 수 있어요.";
    else if (uncertainCompoundAmount(text) ||
      (form === "quote" && reportedAmountError(text) && confirmedAmount === undefined) ||
      (amount === undefined && SAID_AMOUNT.test(text))) {
      issues.amount = "말씀하신 금액을 확인하지 못했어요. 금액을 직접 적어 주세요.";
    }
  }
  if (form === "dates") {
    const parsed = parseDates(text, opts.today, opts.baseMonth, opts.holidays, opts.noWeekend);
    const crew = readCrew(text);
    if (crew.mentioned && (!crew.value || crew.value > (opts.crewMax ?? 6))) {
      issues.crew = "말씀하신 인원을 확정할 수 없어요. 보낼 인원을 다시 골라 주세요.";
    }
    if (DATE_WORD.test(crew.withoutCrew) && !parsed?.days.some((day) => day > opts.today)) {
      issues.days = "말씀하신 날짜를 넣지 못했어요. 달력에서 공사할 날을 다시 골라 주세요.";
    }
  }
  if (form === "period" && DATE_WORD.test(text)) {
    const parsed = parsePeriod(text, opts.today, opts.baseMonth);
    if (!parsed || parsed.start < opts.today) {
      issues.period = "말씀하신 기간을 넣지 못했어요. 달력에서 기간을 다시 눌러 주세요.";
    }
  }
  return issues;
}
