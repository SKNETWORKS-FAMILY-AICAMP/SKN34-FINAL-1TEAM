import Link from "next/link";
import { notFound } from "next/navigation";
import { PlanRedo } from "@/components/project/PlanRedo";
import { PlanTabs } from "@/components/project/PlanTabs";
import { PlanBars, PlanDays, PlanLegend } from "@/components/project/PlanViews";
import { ProjectCrumb } from "@/components/project/ProjectCrumb";
import { getMyProject } from "@/lib/api/project";
import { getPlan } from "@/lib/api/schedule";
import { crewLabel, projectTitle } from "@/lib/labels";
import { dateSeoul, decText, dotDate, md, todaySeoul } from "@/lib/format";
import s from "./page.module.css";

export const metadata = { title: "예상 공정표" };

// 예상 공정표 (시안 WPlan). 날짜 · 기간 · 품 · 인원은 모두 API(계산 엔진) 값이고 화면에서 나누거나 올리지 않는다.
// 확정 공정표(WPlanDone)는 업체 확정 뒤 기능에서 붙인다.
export default async function PlanPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [p, plan] = await Promise.all([getMyProject(id), getPlan(id)]);
  if (!p || !plan) notFound();
  const title = projectTitle(p.region, (["dobae", "mokgong"] as const).filter((t) => p.trades[t]));
  const works = plan.blocks.filter((b) => b.kind === "work");
  const workEnd = works.at(-1)?.end ?? plan.end;
  const dry = plan.blocks.find((b) => b.kind === "dry");

  return (
    <main id="main">
      <ProjectCrumb id={id} title={title} here="예상 공정표" />
      <div className={`mj-wrap ${s.top}`}>
        <div className={s.topL}>
          <div className={s.h}>
            <h1 className="mj-h1">예상 공정표</h1>
            <p className="mj-small mj-num">{dotDate(dateSeoul(plan.computed_at))} 계산</p>
          </div>
          <p className={`mj-num ${s.line}`}>
            <span>일하는 날 <b>{plan.work_days}일</b> · <b>{md(plan.start)} ~ {md(workEnd)}</b></span>
            <span className="mj-badge mj-b-wait">믿집 계산</span>
          </p>
        </div>
        <Link className="mj-btn mj-btn-pri" href={`/projects/${id}/vendors`}>업체 고르기</Link>
      </div>

      <section className={`mj-wrap ${s.views}`} aria-labelledby="pl-t">
        <h2 id="pl-t" className="mj-sr">공정표</h2>
        <PlanTabs
          bars={<><PlanBars plan={plan} /><PlanLegend plan={plan} /></>}
          list={<PlanDays plan={plan} />}
          extra={<Link className="mj-lnk" href={`/projects/${id}/standard`}>표준견적서 보기</Link>}
        />
      </section>

      <div className={`mj-wrap ${s.bottom}`}>
        <div className={s.left}>
          <details className={s.more}>
            <summary>
              기간은 이렇게 계산해요
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M3.5 5.5L7 9l3.5-3.5" /></svg>
            </summary>
            <div className={s.moreIn}>
              <p>일의 양을 하루에 일하는 사람 수로 나누고, 짐 정리 · 마감 같은 반나절 단계를 더해 하루 단위로 올려요. <span className="mj-small">믿집 계산</span></p>
              <p className="mj-small">일의 양은 &lsquo;품&rsquo;으로 세요. 1품은 기능공 1명이 하루에 하는 일이에요.</p>
              <ul className={`mj-num ${s.calc}`}>
                {works.map((b) => (
                  <li key={`${b.trade}-${b.start}`}>
                    <b>{b.label}</b> {b.man_day && b.crew ? `${decText(b.man_day)}품 ÷ ${crewLabel(b.trade ?? "")} ${b.crew}명 → ` : "→ "}<b>{b.days}일</b>
                  </li>
                ))}
              </ul>
              <p className="mj-small">
                {plan.weekend_work ? "주말에도 일하고 공휴일만 쉬어요." : "주말 · 공휴일은 쉬어요."}
                {dry ? ` 도배가 끝나면 ${dry.days}일 동안 창문을 닫고 말린 뒤 입주 청소를 해요.` : " 공사가 끝나면 입주 청소를 해요."}
              </p>
            </div>
          </details>
          <p className="mj-small">예상 일정이에요. 업체를 확정하고 업체가 공사 날짜를 올리면 확정 공정표로 바뀌어요.</p>
        </div>
        <div className={s.right}>
          <PlanRedo id={id} start={plan.start} weekend={plan.weekend_work} today={todaySeoul()} />
        </div>
      </div>
    </main>
  );
}
