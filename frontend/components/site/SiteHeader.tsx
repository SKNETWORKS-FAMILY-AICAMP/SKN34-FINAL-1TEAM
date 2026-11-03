// 모든 화면 맨 위 머리. 로그인한 사람의 역할(비로그인 · 고객 · 업체 · 관리자)마다 머리 하나를 쓴다.
// variant="admin"은 관리자 화면 레이아웃이 넘긴다.
import { getSession } from "@/lib/session";
import { HeaderBar } from "./HeaderBar";

export async function SiteHeader({ variant = "user" }: { variant?: "user" | "admin" }) {
  const session = await getSession();
  // 안 읽은 알림 수는 알림 기능(7번)이 생기면 API에서 읽는다. 지금은 0(배지 없음).
  return <HeaderBar session={session} admin={variant === "admin"} unread={0} />;
}
