// 비밀번호 찾기 · 새 비밀번호 정하기(시안 WResetPw). 화면만 있고 메일 발송 · 토큰은 나중 작업이다.
import { SiteHeader } from "@/components/site/SiteHeader";
import { ResetPassword } from "@/components/auth/ResetPassword";
import s from "@/components/auth/auth.module.css";

export const metadata = { title: "비밀번호 찾기" };

export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ token?: string | string[] }> }) {
  const { token } = await searchParams;
  return (
    <>
      <SiteHeader />
      <main id="main" className={`mj-wrap ${s.page}`}>
        <ResetPassword fromMail={!!token} />
      </main>
    </>
  );
}
