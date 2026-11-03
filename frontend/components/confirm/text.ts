// 업체 확정(시안 WConfirm)과 확정 뒤 모습(비교 · 업체 고르기 · 실측)에만 쓰는 화면 말 · 표시 함수.
// 날짜 · 기한 · 금액은 API(계산 엔진) 값의 형식만 바꾼다. 서버 · 브라우저 양쪽에서 쓴다.
import type { Schemas } from "@/lib/backend/client";
import { md, stampSeoul } from "@/lib/format";

export type Deal = Schemas["DealOut"];
export type ConfirmOut = Schemas["ConfirmOut"];

/** API 시각 → "11.8(일) 10:12" */
export const when = (iso: string | null | undefined) => (iso ? stampSeoul(new Date(iso)) : "");
/** 날짜 "2026-11-16" → "11.16(월)" */
export const day = (iso: string | null | undefined) => (iso ? md(iso.slice(0, 10)) : "");
/** 공사 날짜 "11.16(월) ~ 11.18(수)" (없으면 빈 글) */
export const dates = (d: Pick<Deal, "start_date" | "end_date">) => (d.start_date && d.end_date ? `${day(d.start_date)} ~ ${day(d.end_date)}` : "");

/** 확정 뒤 공사 상태 (고객 쪽 말) */
export const DEAL_LABEL: Record<string, string> = {
  confirmed: "공사 날짜 기다림",
  scheduled: "공사 날짜 수락함",
  checking: "공사 끝냄 · 내 확인 기다림",
  fixing: "남은 일을 알렸어요",
  done: "공사 끝남",
};

/** 업체를 다시 고를 수 있는지 (공사 날짜를 수락하기 전) */
export const canReselect = (d: Pick<Deal, "status">) => d.status === "confirmed";
