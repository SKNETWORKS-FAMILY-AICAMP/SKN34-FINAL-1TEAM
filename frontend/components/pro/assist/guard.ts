import { parseDates, parsePeriod, parseQuote, readCrew, recognizedCompoundAmount, SAID_AMOUNT, uncertainCompoundAmount } from "./parse";
import type { AssistForm, RuleOptions } from "./protocol";

/** 규칙이 한 금액으로 축약하지 못해도 명시적으로 새로 확정한 금액은 서버 후보와 대조해요. */
export function amountToConfirm(text: string, today: string): number | undefined {
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
    else if (uncertainCompoundAmount(text) || (amount === undefined && SAID_AMOUNT.test(text))) {
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
