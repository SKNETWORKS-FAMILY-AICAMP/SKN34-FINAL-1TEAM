// 서버 쪽 읽기: 예상 공정표(실제 API) · 확정 공정표(예시 데이터).
import { serverGet, type Schemas } from "@/lib/backend/server";
import { FX } from "@/lib/mock/data";
import { isUuid } from "./project";

export type ApiPlan = Schemas["PlanOut"];
/** 예상 공정표 (믿집 계산). 없거나 남의 것이면 null */
export async function getPlan(projectId: string) {
  return isUuid(projectId) ? serverGet<ApiPlan>(`/projects/${projectId}/plan`) : null;
}

// 아래는 예시 프로젝트(p1) 화면이 쓰는 예시 데이터

export async function getExpectedSchedule(_projectId: string) { return FX.expected; }
export async function getConfirmedSchedule(_projectId: string) { return FX.confirmed; }
