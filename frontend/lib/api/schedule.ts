// 서버 쪽 읽기: 예상 공정표 · 업체 확정(실제 API), 확정 공정표(예시 데이터).
import { serverGet, type Schemas } from "@/lib/backend/server";
import { FX } from "@/lib/mock/data";
import { isUuid } from "./project";

export type ApiPlan = Schemas["PlanOut"];
/** 예상 공정표 (믿집 계산). 없거나 남의 것이면 null */
export async function getPlan(projectId: string) {
  return isUuid(projectId) ? serverGet<ApiPlan>(`/projects/${projectId}/plan`) : null;
}

export type ApiDeal = Schemas["DealOut"];
/** 이 집 공정의 업체 확정(다시 고른 것은 빠짐). 남의 공사면 null */
export async function listDeals(projectId: string, trade?: "dobae" | "mokgong") {
  return isUuid(projectId) ? serverGet<ApiDeal[]>(`/projects/${projectId}/deals${trade ? `?trade=${trade}` : ""}`) : null;
}

// 아래는 예시 프로젝트(p1) 화면이 쓰는 예시 데이터

export async function getExpectedSchedule(_projectId: string) { return FX.expected; }
export async function getConfirmedSchedule(_projectId: string) { return FX.confirmed; }
