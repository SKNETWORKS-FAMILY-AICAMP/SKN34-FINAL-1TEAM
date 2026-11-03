// 공사 날짜 내기(시안 WVCalendar)에만 쓰는 화면 말 · 표시 함수. 날짜 · 기간 · 기한은 API 값 그대로 형식만 바꾼다.
// 작업함 공사 탭 · 요청 자세히도 같은 말을 쓴다.
import type { Schemas } from "@/lib/backend/client";
import { decText, md, stampSeoul } from "@/lib/format";
import { crewLabel, tradeLabel } from "@/lib/labels";

export type Job = Schemas["JobOut"];
export type Preview = Schemas["PreviewOut"];
export type Need = Schemas["NeedOut"];
export type Other = Schemas["OtherTradeOut"];
export type Method = Schemas["DatesIn"]["method"];
type JobRow = Pick<Schemas["InboxRowOut"], "dates_state" | "dates_due_at" | "overdue" | "start_date" | "end_date" | "proposed_start" | "proposed_end" | "request_due_at">;

/** 하루 작업 인원 한도 (schedule.models CREW_MAX와 같다) */
export const CREW_MAX = 20;

/** 고객이 수락하기 전: 날짜를 내고 다시 낼 수 있다 */
export const isOpen = (st: string | null | undefined) => st === "waiting" || st === "proposed" || st === "requested";

/** 다시 고른 공사 (서버가 409 invalid_state로 주는 문장과 같다) */
export const RESELECTED = "고객이 업체를 다시 고르기로 했어요.";

/** 공사 날짜 화면으로 가는 버튼 말 (수락한 뒤는 '공사 날짜 보기') */
export function goLabel(st: string | null | undefined): string {
  if (st === "waiting") return "공사 날짜 내기";
  if (st === "proposed") return "날짜 다시 내기";
  if (st === "requested") return "요청에 답하기";
  return "공사 날짜 보기";
}

/** 바로 해야 할 일이면 주 버튼 (날짜 내기 · 요청에 답하기) */
export const needsAct = (st: string | null | undefined) => st === "waiting" || st === "requested";

export const METHOD_LABEL: Record<Method, string> = { app: "믿집에서 날짜 내기", call: "고객과 통화로 정함" };

/** "11.25(수) ~ 11.27(금)" (하루면 "11.25(수)") */
export const span = (a: string, b: string) => (a === b ? md(a) : `${md(a)} ~ ${md(b)}`);

/** 기한 "11.6(금) 18:00" (API 시각 그대로, 서울) */
export const stamp = (iso: string) => stampSeoul(new Date(iso));

/** 반나절 수를 읽기 쉽게: 4 → "2일", 1 → "반나절", 3 → "1일 반나절" (같은 값의 단위만 바꾼다) */
export function halfText(n: number): string {
  const day = Math.floor(n / 2);
  if (n % 2 === 0) return `${day}일`;
  return day ? `${day}일 반나절` : "반나절";
}

/** 고객에게 보이는 정한 방법: "통화로 정함 · 11.3(화) 통화" · "믿집에서 냄" */
export function methodText(method: string, callOn: string | null | undefined): string {
  if (method === "call") return callOn ? `통화로 정함 · ${md(callOn)} 통화` : "통화로 정함";
  return "믿집에서 냄";
}

/** 받침에 맞춰 은/는: "도배는", "목공은" */
export function eunNeun(word: string): string {
  const c = word.charCodeAt(word.length - 1);
  if (c < 0xac00 || c > 0xd7a3) return `${word}은(는)`;
  return `${word}${(c - 0xac00) % 28 === 0 ? "는" : "은"}`;
}

/** 필요한 기간 풀이: "도배 7.5품 · 도배공 4명 → 벽지 바르기 2일 + 마감 반나절 → 3일" (API 값을 늘어놓기만) */
export function needText(trade: string, need: Need): string {
  const steps = need.steps.map((x) => `${x.name} ${halfText(x.half_days)}`).join(" + ");
  return `${tradeLabel(trade)} ${decText(need.man_day)}품 · ${crewLabel(trade)} ${need.crew}명 → ${steps} → ${need.days}일.`;
}

/** 작업함 · 요청 자세히의 공사 날짜 한 줄 (기한 · 날짜는 API 값) */
export function datesLine(r: JobRow): string {
  const st = r.dates_state;
  if (st === "waiting") {
    if (!r.dates_due_at) return "공사 날짜를 내 주세요.";
    return r.overdue
      ? `공사 날짜를 낼 기한(${stamp(r.dates_due_at)})이 지났어요. 자동으로 취소되지 않아요.`
      : `공사 날짜를 ${stamp(r.dates_due_at)}까지 내 주세요.`;
  }
  if (st === "proposed") {
    const when = r.proposed_start && r.proposed_end ? `낸 날짜 ${span(r.proposed_start, r.proposed_end)} · ` : "";
    return `${when}고객 수락을 기다려요. 수락하기 전까지 다시 낼 수 있어요.`;
  }
  if (st === "requested") {
    if (!r.request_due_at) return "고객이 다른 공사 날짜를 원해요.";
    return r.overdue
      ? `고객이 다른 공사 날짜를 원해요. 답할 기한(${stamp(r.request_due_at)})이 지났어요.`
      : `고객이 다른 공사 날짜를 원해요. ${stamp(r.request_due_at)}까지 답해 주세요.`;
  }
  if (st === "closed") return RESELECTED;
  return r.start_date && r.end_date ? `공사 날짜 확정 ${span(r.start_date, r.end_date)}` : "공사 날짜가 확정됐어요.";
}
