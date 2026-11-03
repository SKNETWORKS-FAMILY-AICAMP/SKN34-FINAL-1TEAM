// 머리 메뉴 · 계정 메뉴 목록. 역할마다 머리 하나를 글자 그대로 쓴다(시안 WSystem '머리').
// 서버(SiteHeader)와 브라우저(NavLinks · MobileMenu · error.tsx) 양쪽에서 쓴다.
import type { Role, Session } from "@/lib/types";

export type HeaderKind = "guest" | Role;
export type NavItem = { id: string; href: string; label: string };

// '시공 상담'(챗)은 보류라 넣지 않는다. 업체 찾기는 내 공사에서 프로젝트를 고른 뒤 업체를 고른다.
export const NAV: Record<HeaderKind, NavItem[]> = {
  guest: [
    { id: "start", href: "/start", label: "견적 받기" },
    { id: "faq", href: "/faq", label: "공사 질문" },
  ],
  customer: [
    { id: "start", href: "/start", label: "견적 받기" },
    { id: "my", href: "/my", label: "내 공사" },
    { id: "find", href: "/my", label: "업체 찾기" },
  ],
  vendor: [
    { id: "inbox", href: "/vendor", label: "작업함" },
    { id: "jobs", href: "/vendor/jobs", label: "일정" },
    { id: "profile", href: "/vendor/profile", label: "업체 정보" },
  ],
  admin: [
    { id: "review", href: "/admin/vendors", label: "업체 심사" },
    { id: "members", href: "/admin/members", label: "회원관리" },
  ],
};

// 계정 메뉴(로그아웃은 따로 붙인다)
export const ACCOUNT: Record<Role, NavItem[]> = {
  customer: [
    { id: "my", href: "/my", label: "내 공사" },
    { id: "account", href: "/account", label: "내 정보" },
  ],
  vendor: [
    { id: "profile", href: "/vendor/profile", label: "업체 정보" },
    { id: "account", href: "/account", label: "내 정보" },
  ],
  admin: [],
};

export const LOGO_HREF: Record<HeaderKind, string> = { guest: "/", customer: "/", vendor: "/vendor", admin: "/admin/vendors" };
export const LOGO_TAG: Partial<Record<HeaderKind, string>> = { vendor: "업체", admin: "관리자" };

export function accountLabel(s: Pick<Session, "name" | "role">): string {
  return s.role === "admin" ? "관리자 계정" : `${s.name} 님`;
}

const VENDOR_PICK = /^\/projects\/[^/]+\/vendors(\/|$)/;

/** 지금 보는 화면이 이 메뉴인지 (aria-current="page") */
export function isCurrent(item: NavItem, path: string): boolean {
  switch (item.id) {
    case "my":
      return path === "/my" || (path.startsWith("/projects/") && !VENDOR_PICK.test(path));
    case "find":
      return VENDOR_PICK.test(path);
    case "inbox":
      return path === "/vendor" || path.startsWith("/vendor/requests");
    default:
      return path === item.href || path.startsWith(item.href + "/");
  }
}
