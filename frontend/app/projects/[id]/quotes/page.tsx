// 실측 견적 비교 (시안 WCompare): 공정 탭(받은 수) · 표준견적서 합계 · 업체마다 보낸 최신 실측 견적서(파일 · 낼 금액 · 칸 · 이전 버전).
// 읽기는 GET /projects/{id}/quotes?trade= 하나. 금액 · 표준 대비 % · 바뀐 칸 · 유효기간 지남은 API 값을 그대로 보인다.
// 확정 전에는 견적서가 온 업체마다 '이 업체로 확정하기'(확정 화면), 확정한 뒤에는 확정 · 공사 날짜 기한 줄(시안 WConfirm '확정한 뒤')을 보인다.
import Link from "next/link";
import { notFound } from "next/navigation";
import { CompareTabs } from "@/components/compare/CompareTabs";
import { QuoteCard } from "@/components/compare/QuoteCard";
import { DealStatus } from "@/components/confirm/DealStatus";
import { ProjectCrumb } from "@/components/project/ProjectCrumb";
import { isTrade, type VTrade } from "@/components/vendors/text";
import { getMyProject } from "@/lib/api/project";
import { getQuoteCompare } from "@/lib/api/quote";
import { priceVerLabel, projectTitle, tradeLabel } from "@/lib/labels";
import { won } from "@/lib/format";
import s from "./page.module.css";

export const metadata = { title: "실측 견적 비교" };

export default async function QuotesPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ trade?: string }> }) {
  const { id } = await params;
  const { trade: asked } = await searchParams;
  const p = await getMyProject(id);
  if (!p) notFound();
  const trades = (["dobae", "mokgong"] as VTrade[]).filter((t) => p.trades[t]);
  if (!trades.length) notFound();
  const trade = isTrade(asked) && trades.includes(asked) ? asked : trades[0];
  const c = await getQuoteCompare(id, trade);
  if (!c) notFound();

  const tName = tradeLabel(trade);
  const here = `${tName} 실측 견적 비교`;
  const pickHref = `/projects/${id}/vendors?trade=${trade}`;
  const deal = c.deal;
  const chosen = !!deal || c.vendors.some((v) => v.status === "chosen");
  const confirmHref = (r: (typeof c.vendors)[number]) =>
    !chosen && r.status === "quoted" && r.latest && r.seen_ver != null ? `/projects/${id}/quotes/${r.visit_id}/confirm?trade=${trade}` : null;

  return (
    <main id="main">
      <ProjectCrumb id={id} title={projectTitle(p.region, trades)} here={here} />
      <div className={`mj-wrap ${s.title}`}>
        <h1 className="mj-h1">{here}</h1>
      </div>

      <div className={`mj-wrap ${s.body}`}>
        <div className={s.top}>
          <CompareTabs base={`/projects/${id}/quotes`} trades={trades} current={trade} counts={c.sent_counts} />
          <section className={s.std} aria-labelledby="std-t">
            <div className={s.stdMain}>
              <h2 id="std-t" className={`mj-h2 ${s.stdH}`}>표준견적서 <span className={s.chip}>믿집 계산</span></h2>
              <p className="mj-num"><span className={s.big}>{won(c.standard_total)}</span> <span className="mj-small">부가세 포함</span></p>
              <p className="mj-small">{priceVerLabel(c.price_ver)} 공공 기준 단가로 믿집이 계산한 참고 금액이에요.</p>
            </div>
            <Link className="mj-lnk" href={`/projects/${id}/standard`}>표준견적서 보기</Link>
          </section>
        </div>

        <section aria-labelledby="vq-t" className={s.sec}>
          <div className={s.secHead}>
            <h2 id="vq-t" className="mj-h2">업체가 보낸 실측 견적서</h2>
            <p className="mj-small">업체 양식 파일 그대로예요. 파일을 열어 보고 비교해 주세요.</p>
          </div>
          {deal ? <div className={s.deal}><DealStatus d={deal} /></div> : null}
          {c.vendors.length ? (
            <ul role="list" className={s.cards}>
              {c.vendors.map((r) => <QuoteCard key={r.visit_id} r={r} projectId={id} trade={trade} tradeName={tName} confirmHref={confirmHref(r)} />)}
            </ul>
          ) : (
            <div className={s.empty}>
              <p>아직 {tName} 실측을 요청한 업체가 없어요.</p>
              <Link className="mj-btn mj-btn-pri" href={pickHref}>업체 고르기</Link>
            </div>
          )}
          {c.vendors.length && !chosen ? (
            <div className={s.after}>
              <Link className="mj-lnk" href={pickHref}>다른 업체에 실측 더 요청하기</Link>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
