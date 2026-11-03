// 작업함 · 요청 자세히(시안 WVInbox)에만 쓰는 화면 말 · 표시 함수. 날짜 · 금액은 새로 계산하지 않고 형식만 바꾼다.
import type { Schemas } from "@/lib/backend/client";
import { dateSeoul, decText, md } from "@/lib/format";
import { gradeLabel, tradeLabel } from "@/lib/labels";

export type Row = Schemas["InboxRowOut"];
export type Detail = Schemas["VendorVisitDetailOut"];
export type Tab = "todo" | "waiting" | "work" | "done";

export const TABS: { k: Tab; t: string }[] = [
  { k: "todo", t: "할 일" },
  { k: "waiting", t: "기다림" },
  { k: "work", t: "공사" },
  { k: "done", t: "끝남" },
];

/** 탭이 비었을 때 */
export const EMPTY: Record<Tab, string> = {
  todo: "지금 할 일이 없어요. 새 실측 요청이 오면 알림으로 알려 드려요.",
  waiting: "고객의 결정을 기다리는 일이 없어요.",
  work: "확정된 공사가 없어요.",
  done: "끝난 일이 아직 없어요.",
};

/** 탭 위 한 줄 안내 */
export const TAB_NOTE: Partial<Record<Tab, string>> = {
  waiting: "고객이 확정하면 공사 탭으로, 다른 업체로 정하면 끝남 탭으로 가요.",
  work: "확정된 공사예요. 공사 날짜를 내고 바꾸고, 추가 금액과 공사 끝남을 여기서 알려요.",
  done: "고르지 않았거나 취소된 요청은 닫히는 즉시 고객 이름과 연락처를 가려요. 공사가 끝난 고객은 끝냈다고 올린 날부터 1년까지 보여요.",
};

/** 끝남 탭 다음 쪽 크기 (API 기본값과 같다) */
export const PAGE = 20;

/** 닫힌 요청이 어떻게 끝났나 */
export function endLabel(v: Pick<Row, "status" | "close_reason">): string {
  if (v.status === "cancelled") return "요청이 취소됐어요";
  if (v.status === "declined") return "내가 받지 않았어요";
  if (v.close_reason === "reselected") return "고객이 업체를 다시 고르기로 했어요";
  return "다른 업체로 정했어요";
}

/** "10.29(목) 닫힘 · 연락처를 가렸어요" */
export function endNote(v: Pick<Row, "closed_at">): string {
  return v.closed_at ? `${md(dateSeoul(v.closed_at))} 닫힘 · 연락처를 가렸어요` : "닫힘 · 연락처를 가렸어요";
}

export const isDone = (status: string) => status === "cancelled" || status === "declined" || status === "closed";

/** 받은 날 "11.02(월)" */
export const dayOf = (iso: string) => md(dateSeoul(iso));

/** "32평" (평은 API가 표준견적서에 저장한 값) */
export const pyeongText = (p: string | null) => (p ? `${decText(p)}평` : "—");

/** "도배 기본형" · "목공" */
export function workText(v: Pick<Row, "trade" | "grade">): string {
  return v.trade === "dobae" && v.grade ? `${tradeLabel(v.trade)} ${gradeLabel(v.grade)}` : tradeLabel(v.trade);
}

/** 고객 이름 또는 요청 번호 (확인 창 · 화면 읽기 이름) */
export const whoOf = (v: Pick<Row, "customer" | "code">) => v.customer?.name ?? v.code;

/** 전화 걸기 주소 "tel:01023456789" */
export const telHref = (phone: string) => `tel:${phone.replace(/\D/g, "")}`;

/** 받침에 맞춰 이/가를 붙인다: "마루와벽" → "마루와벽이", "도배나라" → "도배나라가". 숫자로 끝나면 읽는 소리로 본다 */
export function iGa(word: string): string {
  const last = word.slice(-1);
  if (/[0-9]/.test(last)) return `${word}${"2459".includes(last) ? "가" : "이"}`; // 이 · 사 · 오 · 구는 받침이 없다
  const c = word.charCodeAt(word.length - 1);
  if (c < 0xac00 || c > 0xd7a3) return `${word}이(가)`;
  return `${word}${(c - 0xac00) % 28 === 0 ? "가" : "이"}`;
}
