"use client";
// 머리 주 메뉴 링크. 지금 보는 화면에 aria-current="page"를 붙인다.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV, isCurrent, type HeaderKind } from "./nav";

export function NavLinks({ kind, className }: { kind: HeaderKind; className?: string }) {
  const path = usePathname();
  return (
    <>
      {NAV[kind].map((l) => (
        <Link key={l.id} href={l.href} className={className} aria-current={isCurrent(l, path) ? "page" : undefined}>{l.label}</Link>
      ))}
    </>
  );
}
