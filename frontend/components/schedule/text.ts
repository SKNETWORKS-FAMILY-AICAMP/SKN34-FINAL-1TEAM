// 공사 날짜(시안 WSchedule)에만 쓰는 화면 말 · 표시 함수. 날짜 · 일하는 날 · 기한은 API(계산 엔진) 값의 형식만 바꾼다.
// 서버 · 브라우저 양쪽에서 쓴다.
import { DEAL_LABEL, day } from "@/components/confirm/text";
import type { Schemas } from "@/lib/backend/client";

export type Schedule = Schemas["ScheduleOut"];
export type TradeDates = Schemas["TradeDatesOut"];
export type Proposal = Schemas["ProposalOut"];
export type DateReq = Schemas["DateRequestOut"];
export type StartOption = Schemas["StartOptionOut"];
export type Holiday = Schemas["HolidayOut"];

/** "11.19(목) ~ 11.23(월)" (하루면 그날만) */
export const span = (start: string, end: string) => (start === end ? day(start) : `${day(start)} ~ ${day(end)}`);

/** 공사 날짜를 수락했거나 그 뒤 공사 상태인지 (확정 상태로 본다. 수락 뒤 날짜 바꾸기는 확정 공정표에서 다룬다) */
export const isAccepted = (t: TradeDates) => t.deal.status !== "confirmed";

/** 정한 방법: "통화로 정함 · 11.8(일) 통화" · "믿집에서 냄" */
export const methodText = (x: Pick<Proposal, "method" | "call_on">) =>
  x.method === "call" ? `통화로 정함${x.call_on ? ` · ${day(x.call_on)} 통화` : ""}` : "믿집에서 냄";

/** 쉬는 날 말 (주말에도 공사하면 공휴일만 쉰다) */
export const offText = (weekendWork: boolean) => (weekendWork ? "공휴일만 쉬어요" : "주말 · 공휴일은 쉬어요");

/** 낸 날짜 안의 공휴일 (API가 준 공휴일 목록에서 고른다) */
export const holidaysIn = (hs: Holiday[], start: string, end: string) => hs.filter((h) => h.day >= start && h.day <= end);

export type Tone = "ok" | "wait" | "now" | "bad" | "off";

/** 공정 탭 · 얹어 보기 표의 상태 (확정이 없으면 '확정 전') */
export function stateOf(t: TradeDates | undefined): { text: string; tone: Tone } {
  if (!t) return { text: "확정 전", tone: "off" };
  if (isAccepted(t)) {
    // 수락한 뒤 날짜 바꾸기(확정 공정표에서 다룬다)
    if (t.dates_state === "change_requested") return { text: "날짜 바꾸기 요청함", tone: "wait" };
    if (t.dates_state === "change_proposed") return { text: "바꿀 날짜 옴 · 수락 전", tone: "now" };
    return { text: t.deal.status === "scheduled" ? "수락함" : (DEAL_LABEL[t.deal.status] ?? "수락함"), tone: "ok" };
  }
  switch (t.dates_state) {
    case "waiting":
      return t.deal.overdue ? { text: "기한 지남", tone: "bad" } : { text: "날짜 기다림", tone: "wait" };
    case "proposed":
      return { text: t.proposal?.changed ? "새 날짜 · 수락 전" : "날짜 옴 · 수락 전", tone: "now" };
    case "requested":
      return t.request?.overdue ? { text: "요청 답 기한 지남", tone: "bad" } : { text: "다른 날짜 요청함", tone: "wait" };
    default:
      return { text: "날짜 기다림", tone: "wait" };
  }
}
