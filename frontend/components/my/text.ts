// 내 공사 목록(시안 WMy)에만 쓰는 화면 말. 단계 · 할 일은 서버 값을 그대로 쓰고 이름만 붙인다.
import type { ApiProjectItem } from "@/lib/api/project";

export type Phase = ApiProjectItem["phase"];

/** 공사 전체 단계 이름 */
export const PHASE_TEXT: Record<Phase, string> = { estimate: "견적만 받음", progress: "진행 중", work: "공사 날짜 확정", done: "끝남" };
/** 단계 표시 색 (globals.css mj-st-*) */
export const PHASE_TONE: Record<Phase, string> = { estimate: "mj-st-off", progress: "mj-st-now", work: "mj-st-now", done: "mj-st-ok" };

/** 6단계 이름. current(1부터)로 찾는다 */
const STEP_NAMES = ["집 정보 · 공정", "기준 계산", "업체 고르기", "실측", "업체 확정", "공사 날짜"];

/** "6단계 중 2 · 기준 계산" */
export function stepText(current: number) {
  const name = STEP_NAMES[current - 1];
  return name ? `6단계 중 ${current} · ${name}` : `6단계 중 ${current}`;
}
