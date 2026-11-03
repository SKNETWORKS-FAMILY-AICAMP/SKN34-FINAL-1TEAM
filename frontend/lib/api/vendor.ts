// 서버 쪽 읽기: 업체 본인(가입 심사 · 업체 정보 · 가견적 단가)은 실제 API, 고객이 보는 업체 목록 · 상세 · 추천은 예시 데이터.
import { serverGet, type Schemas } from "@/lib/backend/server";
import { RECOMMEND, VENDORS } from "@/lib/mock/data";
import type { Trade, Vendor } from "@/lib/types";

export type VendorApplicationMine = Schemas["ApplicationOut"];
export type VendorProfile = Schemas["ProfileOut"];
export type VendorPrices = Schemas["PricesOut"];
/** 내 가입 신청 · 심사 결과 (업체 본인, 승인 전에도) */
export async function getMyApplication() { return serverGet<VendorApplicationMine>("/vendor/application"); }
/** 내 업체 정보 (승인된 업체만, 아니면 null) */
export async function getMyProfile() { return serverGet<VendorProfile>("/vendor/profile"); }
/** 내 가견적 단가 · 근거 파일, 공정 하나 (승인된 업체만, 아니면 null) */
export async function getMyPrices(trade: "dobae" | "mokgong") {
  return serverGet<VendorPrices>(`/vendor/prices?trade=${trade}`);
}

export async function getRecommendations(_projectId: string, trade: Trade) { return RECOMMEND[trade]; }

export type VendorQuery = { trade: Trade; region?: string; q?: string; sort?: "reviews" | "rating" | "cases" };
export async function listVendors({ trade, region, q, sort = "reviews" }: VendorQuery) {
  let list = VENDORS.filter((v) => v.trades.includes(trade) && v.status === "active");
  if (region) list = list.filter((v) => v.areas.includes(region));
  if (q?.trim()) list = list.filter((v) => v.name.includes(q.trim()));
  const key: Record<string, (v: Vendor) => number> = { reviews: (v) => v.reviews, rating: (v) => v.rating, cases: (v) => v.portfolio.length };
  return [...list].sort((a, b) => key[sort](b) - key[sort](a));
}
export async function getVendor(id: string) { return VENDORS.find((v) => v.id === id) ?? null; }
export async function listAllVendors() { return VENDORS; }

export async function vendorName(id: string) { return VENDORS.find((v) => v.id === id)?.name ?? id; }
export const ALL_REGIONS = Array.from(new Set(VENDORS.flatMap((v) => v.areas))).sort((a, b) => a.localeCompare(b, "ko"));
