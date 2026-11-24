import type { AssistCurrent, AssistResponse } from "./protocol";

export type PeriodIssueScope = "boundary" | "excluded";
export type PeriodHolds = Partial<Record<PeriodIssueScope, string>>;

const DATE_WORD = /\d\s*(?:일|월|\/)|\d{4}[-.]\d{1,2}[-.]\d{1,2}|오늘|내일|모레|이번\s*주|다음\s*주|요일/;
const EXCLUSION = /제외|휴무|쉬는\s*(?:날|걸|것)|쉬어요|쉽니다|빼는\s*날|불가|안\s*(?:돼|되|됩|됨)|못\s*(?:가|오|하)|어려워|어렵|힘들/;
const BOUNDARY = /시작|종료|마감|끝나는|첫날|마지막날|부터|까지|[~∼〜～]|기간/;
const MANUAL_KEEP = /^(?:시작일|종료일|마감\s*날짜|마감일|끝나는\s*날)(?:은|는)?\s*(?:제가\s*)?(?:방금\s*)?직접\s*(?:수정|변경|고쳤)[^,.!?]*?(?:그\s*값|현재\s*값)(?:은|을)?\s*(?:그대로\s*)?(?:유지|두)[^,.!?]*$/;

/** 날짜가 있는 절이 모두 제외일을 가리킬 때만 기간 경계 보류와 구분해요. */
export function periodIssueScope(text: string): PeriodIssueScope {
  const clauses = text.split(/[,;!?？。\n]|(?<!\d)\.|\.(?!\d)/).map((s) => s.trim()).filter(Boolean);
  const dated = clauses.filter((s) => DATE_WORD.test(s));
  if (!dated.length || !dated.every((s) => EXCLUSION.test(s) && !BOUNDARY.test(s))) return "boundary";
  if (clauses.some((s) => BOUNDARY.test(s) && !MANUAL_KEEP.test(s))) return "boundary";
  return "excluded";
}

/** 적용한 필드의 보류만 해제해요. 미확정 경계 또는 이전 경계 보류는 제외일 적용으로 풀리지 않아요. */
export function resolvePeriodHolds(holds: PeriodHolds, changes: AssistCurrent, response: AssistResponse): PeriodHolds {
  const next = { ...holds };
  const boundaries = response.fields.filter((f) => f.field === "start" || f.field === "end");
  const settled = boundaries.length === 2 && boundaries.every((f) =>
    f.field in changes || (f.status === "valid" && f.action === "keep"));
  if (!settled) return next;
  const excluded = response.fields.find((f) => f.field === "excluded");
  if ("start" in changes || "end" in changes) {
    // 옛 범위 보류에 제외일 문제도 섞였으면 경계 적용 후에도 남겨요.
    if (next.boundary && excluded && excluded.status !== "valid" &&
        (excluded.action === "uncertain" || excluded.evidence.trim())) next.excluded ??= next.boundary;
    delete next.boundary;
  }
  if ("excluded" in changes && excluded?.status === "valid" &&
      (excluded.action === "set" || excluded.action === "clear")) delete next.excluded;
  return next;
}

type CalendarPeriod = { start: string | null; end: string | null; excluded: string[] };

/** 달력에서 직접 선택한 값만 보류 해제 대상으로 삼아요. */
export function manuallyEditedPeriodFields(previous: CalendarPeriod, next: CalendarPeriod): string[] {
  const edited: string[] = [];
  // 시작을 다시 누르면 같은 시작값이라도 새로운 기간 선택을 시작한 거예요.
  if (next.start !== null && (next.start !== previous.start || next.end === null)) edited.push("start");
  if (next.end !== null && next.end !== previous.end) edited.push("end");
  if (next.excluded.length !== previous.excluded.length ||
      next.excluded.some((day, i) => day !== previous.excluded[i])) edited.push("excluded");
  return edited;
}
