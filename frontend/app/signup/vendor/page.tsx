import { redirect } from "next/navigation";
import { SiteHeader } from "@/components/site/SiteHeader";
import { VendorSignupForm, type Again } from "@/components/auth/VendorSignupForm";
import { listRegions } from "@/lib/api/project";
import { getMyApplication } from "@/lib/api/vendor";
import { getSession, requireRole } from "@/lib/session";
import s from "./page.module.css";

export const metadata = { title: "업체 가입 신청" };

function Aside() {
  return (
    <aside aria-label="심사 안내" className={s.aside}>
      <div className={s.box}>
        <h2 className="mj-h3">심사는 이렇게 해요</h2>
        <ol className={s.an}>
          <li><span className="mj-code" aria-hidden="true">1</span><span>관리자가 서류를 확인해요</span></li>
          <li><span className="mj-code" aria-hidden="true">2</span><span>결과를 알림으로 알려요<span className={`mj-small ${s.block}`}>로그인하면 업체 홈 맨 위에도 보여요</span></span></li>
          <li><span className="mj-code" aria-hidden="true">3</span><span>승인되면 견적서를 올리고 실측 요청을 받아요<span className={`mj-small ${s.block}`}>예전에 낸 견적서를 근거로 단가를 넣으면 그 단가로 고객에게 가견적이 보여요</span></span></li>
        </ol>
      </div>
      <div className={s.box}>
        <h2 className="mj-h3">보완 요청을 받으면</h2>
        <p className="mj-small">서류가 흐리거나 상호 · 대표자가 신청서와 다르면 보완 요청이 와요. 업체 홈에서 사유를 보고, 보완 사유에 맞는 칸만 다시 고치면 돼요.</p>
      </div>
      <div className={`${s.box} ${s.ask}`}>
        <h2 className="mj-h3">궁금한 점이 있으면</h2>
        <p className="mj-small">가입에 대해 묻고 싶으면 고객센터로 문의해 주세요.</p>
      </div>
    </aside>
  );
}

// 업체 가입 신청 (시안 WVSignup). ?again=1: 거절된 업체가 로그인한 채 다시 신청(지난 값을 채운다).
export default async function VendorSignupPage({ searchParams }: { searchParams: Promise<{ again?: string }> }) {
  const { again } = await searchParams;
  let data: Again | undefined;
  if (again === "1") {
    const me = await requireRole("vendor", "/signup/vendor?again=1");
    const app = await getMyApplication();
    if (!app || app.status !== "rejected") redirect("/vendor"); // 다시 신청은 거절된 신청만
    data = {
      email: me.email,
      appliedAt: app.applied_at,
      prev: {
        biz_name: app.biz_name ?? "", owner_name: app.owner_name ?? "", phone: app.phone ?? "", biz_no: app.biz_no ?? "",
        trades: app.trades, areas: app.areas.map((a) => ({ sido: a.sido, sgg: a.sgg })), fileName: app.license?.filename ?? null,
      },
    };
  } else if ((await getSession())?.role === "vendor") {
    redirect("/vendor"); // 이미 업체 회원이면 업체 홈에서 심사 상태를 본다
  }
  const regions = await listRegions();
  return (
    <>
      <SiteHeader />
      <main id="main">
        <div className={`mj-wrap ${s.top}`}>
          <div className={s.head}>
            <h1 className="mj-h1">{data ? "업체로 다시 신청해요" : "업체로 가입 신청해요"}</h1>
            <p className="mj-sub">{data ? "지난 신청을 고쳐 다시 내면 관리자가 다시 확인해요." : "기본 정보와 사업자등록증을 내면 관리자가 확인한 뒤 업체 기능이 열려요."}</p>
          </div>
        </div>
        <div className="mj-wrap">
          <VendorSignupForm regions={regions} again={data} aside={<Aside />} />
        </div>
      </main>
    </>
  );
}
