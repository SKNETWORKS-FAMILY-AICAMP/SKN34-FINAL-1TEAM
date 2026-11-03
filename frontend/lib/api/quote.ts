// 서버 쪽 읽기: 표준견적서(실제 API) · 가견적 요청 · 가견적 · 비교 · 실측 견적(예시 데이터).
import { serverGet, type Schemas } from "@/lib/backend/server";
import { FX, QUOTE_REQUESTS } from "@/lib/mock/data";
import type { Trade } from "@/lib/types";
import { isUuid } from "./project";

export type ApiStandard = Schemas["StandardOut"];
/** 표준견적서 (믿집 계산). 없거나 남의 것이면 null */
export async function getStandard(projectId: string) {
  return isUuid(projectId) ? serverGet<ApiStandard>(`/projects/${projectId}/standard`) : null;
}

// 아래는 예시 프로젝트(p1) 화면이 쓰는 예시 데이터

export async function getStandardQuote(_projectId: string) { return { ...FX.standard, createdAt: "2026-10-20" }; }
export async function getQuoteRequests(projectId: string) { return QUOTE_REQUESTS[projectId] ?? []; }
export async function getVendorQuotes(_projectId: string, trade: Trade) { return FX.quotes[trade]; }
export async function getCompare(_projectId: string, trade: Trade) { return FX.compare[trade]; }
export async function getFinalQuote(_projectId: string, trade: Trade) { return FX.finals[trade]; }
