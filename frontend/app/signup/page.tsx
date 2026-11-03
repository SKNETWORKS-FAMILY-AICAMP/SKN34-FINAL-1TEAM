// 이메일 가입(시안 WSignup). '카카오로 시작하기'는 휴대폰 번호 화면(/signup/phone)으로만 간다(카카오 연결은 나중 작업).
import { SiteHeader } from "@/components/site/SiteHeader";
import { SignupForm } from "@/components/auth/SignupForm";
import { DraftAside } from "@/components/auth/DraftAside";
import { AuthTabs, VendorJoin } from "@/components/auth/parts";
import s from "@/components/auth/auth.module.css";

export const metadata = { title: "가입" };

export default async function SignupPage({ searchParams }: { searchParams: Promise<{ next?: string | string[] }> }) {
  const raw = (await searchParams).next;
  const next = Array.isArray(raw) ? raw[0] : raw;
  return (
    <>
      <SiteHeader />
      <main id="main" className={`mj-wrap ${s.page}`}>
        <div className={s.cols}>
          <div className={s.col}>
            <h1 className="mj-sr">믿집 가입</h1>
            <AuthTabs current="signup" next={next} />
            <SignupForm next={next} />
            <VendorJoin />
          </div>
          <DraftAside action="가입" />
        </div>
      </main>
    </>
  );
}
