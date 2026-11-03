// API 코드값 ↔ 화면 말. 질문 흐름(보내기)과 프로젝트 화면(보이기)이 같이 쓴다.
import type { components } from "./backend/schema";

type S = components["schemas"];
export type Budget = NonNullable<S["ProjectCreate"]["budget"]>;
export type MokItem = S["MokgongIn"]["items"][number];
export type DobaeGrade = S["DobaeIn"]["grade"];

export const BUDGETS: { key: Budget; label: string }[] = [
  { key: "u300", label: "300만 원 이하" },
  { key: "300-500", label: "300만~500만 원" },
  { key: "500-700", label: "500만~700만 원" },
  { key: "700-1000", label: "700만~1,000만 원" },
  { key: "o1000", label: "1,000만 원 넘게" },
  { key: "undecided", label: "아직 정하지 않았어요" },
];

export const MOK_ITEMS: { key: MokItem; label: string; desc: string }[] = [
  { key: "molding", label: "천장 몰딩", desc: "교체" },
  { key: "base", label: "걸레받이", desc: "교체" },
  { key: "door", label: "방문 교체", desc: "문틀 · 문짝" },
  { key: "flatten", label: "천장 평탄화", desc: "석고보드" },
];

// 도배 유형. 계산 엔진은 저가형을 합지, 기본형을 실크로 계산한다(backend/app/calc/dobae.py).
export const DOBAE_GRADES: { key: DobaeGrade; label: string; desc: string }[] = [
  { key: "economy", label: "저가형", desc: "종이 벽지(합지)예요. 얇아서 벽 굴곡이 비칠 수 있어요." },
  { key: "basic", label: "기본형", desc: "실크 벽지예요. 도톰하고 물걸레로 닦을 수 있어요." },
];

export const WHEN_LABEL = { date: "날짜가 정해졌어요", movein: "이사하는 날만 정해졌어요", months: "1~2달 안에", unknown: "아직 몰라요" } as const;

export function sizeLabel(s: { band?: string | null; basis?: string | null; m2?: string | number | null }) {
  if (s.band) return s.band === "50" ? "50평 이상" : `${s.band}평대`;
  return `${s.basis === "exclusive" ? "전용" : "공급"} ${s.m2}㎡`;
}
export const budgetLabel = (k: string | null | undefined) => BUDGETS.find((b) => b.key === k)?.label ?? "정하지 않음";
export const mokLabel = (k: string) => MOK_ITEMS.find((m) => m.key === k)?.label ?? k;
export const gradeLabel = (k: string | null | undefined) => DOBAE_GRADES.find((g) => g.key === k)?.label ?? "유형 미정";

export const tradeLabel = (k: string) => (k === "dobae" ? "도배" : k === "mokgong" ? "목공" : k);
/** 공정별 기능공 이름 (공정표 '기간은 이렇게 계산해요') */
export const crewLabel = (k: string) => (k === "dobae" ? "도배공" : k === "mokgong" ? "목수" : "기능공");

/** 견적 줄 포함 상태 6종 (AGENTS.md 3장). 합계에는 '포함'과 '따로 계산'만 들어간다 */
export const LINE_STATES: Record<string, string> = {
  included: "포함", bundled: "묶음 포함", separate: "따로 계산", optional: "선택", after_visit: "실측 후", excluded: "안 함",
};
export const lineStateLabel = (k: string) => LINE_STATES[k] ?? "확인 중";
/** 금액이 있어도 합계 밖인 줄: 괄호 금액으로 보인다 */
export const outsideTotal = (k: string) => k === "optional" || k === "after_visit";

/** 단가 버전 "2026H2" → "2026 하반기" */
export function priceVerLabel(v: string) {
  const m = /^(\d{4})H([12])$/.exec(v);
  return m ? `${m[1]} ${m[2] === "1" ? "상반기" : "하반기"}` : v;
}

/** 이동 경로 · 서류 머리의 공사 이름: "서울 노원구 · 도배 · 목공" */
export function projectTitle(region: string, trades: string[]) {
  const t = ["dobae", "mokgong"].filter((k) => trades.includes(k)).map(tradeLabel);
  return [region, ...t].join(" · ");
}
