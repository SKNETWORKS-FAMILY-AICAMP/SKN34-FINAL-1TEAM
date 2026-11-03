// 업체 자세히 (시안 WVendorDetail · 페이지형): 믿집이 확인한 것, 실측 · 연락, 가견적 '어떻게 계산했나요', 시공 사례,
// 자주 묻는 5가지, 사업자 · 자격 정보. 가견적 줄은 API(계산 엔진) 값을 그대로 보이고 화면에서 곱하거나 더하지 않는다.
// 요청한 뒤에는 실측 · 연락 카드가 펼쳐진다(연락처는 GET /projects/{id}/visits의 이 업체 요청).
// 이 공정을 확정했으면(GET /projects/{id}/deals) 업체 고르기와 같은 확정 안내를 두고 '실측 요청'을 막는다.
import Link from "next/link";
import { notFound } from "next/navigation";
import { DealNote } from "@/components/confirm/DealNote";
import { ContactCard } from "@/components/vendors/ContactCard";
import { DetailRequest } from "@/components/vendors/DetailRequest";
import { Check, Chev, Dash, Lock, Pen, VisitState } from "@/components/vendors/icons";
import { FAQ_LABEL, areasText, asMethod, callHours, day, daySeoul, declineText, eunNeun, feeText, gwaWa, iGa, isLive, isTrade, shutKind, vsText, when, type VTrade } from "@/components/vendors/text";
import { getMyProject } from "@/lib/api/project";
import { listDeals } from "@/lib/api/schedule";
import { getProjectVendor, listProjectVisits } from "@/lib/api/visit";
import { CONTACT_LABEL, bizNoText, projectTitle, tradeLabel } from "@/lib/labels";
import { decText, dotDate, numText, won, wonFull } from "@/lib/format";
import s from "./page.module.css";

export const metadata = { title: "업체 자세히" };

function Mark({ kind }: { kind: "ok" | "self" | "none" }) {
  if (kind === "ok") return <span className="mj-st mj-st-ok"><Check />믿집 확인</span>;
  if (kind === "self") return <span className="mj-st mj-st-off"><Pen />업체가 적음</span>;
  return <span className="mj-st mj-st-wait"><Dash />확인 안 됨</span>;
}

export default async function VendorDetailPage({ params, searchParams }: { params: Promise<{ id: string; vid: string }>; searchParams: Promise<{ trade?: string; sent?: string }> }) {
  const { id, vid } = await params;
  const { trade: asked, sent } = await searchParams;
  const p = await getMyProject(id);
  if (!p) notFound();
  const trades = (["dobae", "mokgong"] as VTrade[]).filter((t) => p.trades[t]);
  if (!trades.length) notFound();
  const trade = isTrade(asked) && trades.includes(asked) ? asked : trades[0];
  const [d, visits, deals] = await Promise.all([getProjectVendor(id, vid, trade), listProjectVisits(id, trade), listDeals(id, trade)]);
  if (!d) notFound();
  const deal = deals?.[0] ?? null;

  const pr = d.profile;
  const name = pr.biz_name ?? "업체";
  const title = projectTitle(p.region, trades);
  const listHref = `/projects/${id}/vendors?trade=${trade}`;
  const mine = visits?.visits.find((v) => v.vendor_id === vid) ?? null; // 최근 순이라 첫 것이 가장 최근
  const live = mine && isLive(mine.status) ? mine : null;
  const text = visits?.request_texts.find((t) => t.trade === trade)?.text;
  const full = (visits?.slots_left[trade] ?? 0) <= 0;
  const m = asMethod(pr.contact_method);
  const hours = callHours(pr.call_start, pr.call_end);
  const e = d.estimate;
  const vs = e ? vsText(e.vs_standard, e.vs_direction) : "";
  const faqs = new Map(pr.faqs.map((f) => [f.key, f.answer]));
  const opened = pr.opened_on ? `${pr.opened_on.slice(0, 7).replace("-", ".")}${d.years_open != null ? ` · 업력 ${d.years_open}년` : ""}` : null;
  const biz: { k: string; v: string; mark: "ok" | "self" | "none" }[] = [
    { k: "상호 · 대표자", v: [name, pr.owner_name].filter(Boolean).join(" · "), mark: "ok" },
    ...(pr.biz_no ? [{ k: "사업자등록번호", v: bizNoText(pr.biz_no), mark: "ok" as const }] : []),
    ...(opened ? [{ k: "개업일", v: opened, mark: "ok" as const }] : []),
    ...(pr.address_area ? [{ k: "사업장", v: pr.address_area, mark: "ok" as const }] : []),
    { k: "실내건축공사업 등록", v: pr.interior_license ?? "적지 않았어요", mark: pr.interior_license ? "self" : "none" },
    { k: "보험", v: pr.insurance ?? "적지 않았어요", mark: pr.insurance ? "self" : "none" },
  ];
  const photoSrc = (fileId: string) => `/api/projects/${id}/vendors/${vid}/photos/${fileId}`;
  const ni = d.not_included;
  const req = (idp: string, hintId?: string) => (
    <DetailRequest projectId={id} trade={trade} vendorId={vid} name={name} consentVer={visits?.consent_ver ?? ""} full={full} idp={idp} hintId={hintId} />
  );

  return (
    <main id="main">
      <nav className={`mj-wrap mj-crumb ${s.crumb}`} aria-label="이동 경로">
        <ol>
          <li><Link href="/my">내 공사</Link><span aria-hidden="true">›</span></li>
          <li><Link href={`/projects/${id}`}>{title}</Link><span aria-hidden="true">›</span></li>
          <li><Link href={listHref}>업체 고르기</Link><span aria-hidden="true">›</span></li>
          <li><span aria-current="page">{name}</span></li>
        </ol>
      </nav>

      <div className={`mj-wrap ${s.top}`}>
        <div className={s.hd}>
          <h1 className="mj-h1">{name}</h1>
          <p className={s.meta}>{pr.trades.map((t) => <span key={t} className={s.chip}>{tradeLabel(t)}</span>)}<span className="mj-sub">활동 지역 {areasText(pr.areas, d.area_name)}</span></p>
          <details className={s.dtl}>
            <summary>
              <span className={`mj-st mj-st-ok ${s.big}`}><Check size={16} />사업자 확인함{d.verified_on ? <span className="mj-num"> · {dotDate(d.verified_on)}</span> : null}</span>
              <Chev className={s.chev} /><span className="mj-sr">, 믿집이 확인한 것 보기</span>
            </summary>
            <div className={s.dtlBody}>
              <div>
                <p className="mj-label">믿집이 확인한 것</p>
                <ul><li>사업자등록증 · 상호 · 대표자 · 개업일 {d.verified_on ? <span className="mj-sub mj-num">{dotDate(d.verified_on)}</span> : null}</li></ul>
              </div>
              <div>
                <p className="mj-label">확인하지 않은 것</p>
                <ul><li>시공 품질 · 실내건축공사업 등록 · 보험</li></ul>
              </div>
              <p className="mj-sub">믿집 확인은 사업자 정보만이에요. 공사 결과를 보증하지 않아요.</p>
            </div>
          </details>
        </div>

        <section className={s.cc} aria-labelledby="ct-t">
          <h2 id="ct-t" className="mj-h3">실측 · 연락</h2>
          {live ? (
            <>
              {sent === "1" ? <p className="mj-notice" role="status"><span><b>보냈어요</b> · {name}에 요청했어요. 업체에 먼저 연락해 주세요.</span></p> : null}
              <ContactCard v={live} projectId={id} trade={trade} requestText={text} allHref={`/projects/${id}/visits?trade=${trade}`} />
            </>
          ) : (
            <>
              {mine ? (
                <p className={s.was}>
                  <VisitState status={mine.status} past={shutKind(mine, !!deal) === "past"} />
                  <span className="mj-small mj-num">{[shutKind(mine, !!deal) === "other" ? "다른 업체로 정했어요" : declineText(mine.decline_code), when(mine.closed_at)].filter(Boolean).join(" · ")}</span>
                </p>
              ) : null}
              <dl className={s.kv}>
                <dt>실측 비용</dt><dd className={s.strong}>{feeText(pr.visit_fee)}{pr.visit_fee && pr.visit_fee_refund ? " · 계약하면 빼 줘요" : ""}</dd>
                <dt>출장 지역</dt><dd>{eunNeun(d.area_name)} 출장 지역 {d.in_area ? "안" : "밖"}이에요</dd>
                {m ? <><dt>연락 방법</dt><dd>{CONTACT_LABEL[m]}</dd></> : null}
                {hours ? <><dt>연락 받는 시간</dt><dd className="mj-num">{hours}</dd></> : null}
              </dl>
              {deal ? <DealNote d={deal} projectId={id} /> : (
                <>
                  <p className={`mj-small ${s.lock}`}><Lock />실측을 요청하면 연락처가 보여요.</p>
                  {req("rq-top", full ? "full-h" : undefined)}
                  <p id="full-h" className="mj-small">{full ? "이미 3곳에 요청했어요. 요청을 취소하거나 업체가 못 받으면 칸이 비어요." : `방문 날짜는 ${gwaWa(name)} 직접 정해요.`}</p>
                </>
              )}
            </>
          )}
        </section>
      </div>

      <div className="mj-wrap">
        <section className={`mj-sec ${s.sec}`} aria-labelledby="qt-t">
          <div className="mj-sec-l"><h2 id="qt-t" className="mj-h2">가견적</h2></div>
          <div className="mj-sec-c">
            {e ? (
              <div className={s.est}>
                <p className={`mj-num ${s.money}`}><span className={s.total}>{won(e.total)}</span><span className="mj-small">부가세 포함</span></p>
                <p className="mj-small">업체가 확인한 단가로 믿집이 계산했어요{vs ? ` · ${vs}` : ""}</p>
                <details className={`${s.dtl} ${s.calcBox}`}>
                  <summary><Chev className={s.chev} />어떻게 계산했나요</summary>
                  <div className={s.dtlBody}>
                    <p className="mj-small">{iGa(name)} <span className="mj-num">{day(e.confirmed_on)}</span>에 확인한 단가예요. 단가는 부가세 빼고, 수량은 표준견적서와 같아요.</p>
                    <ul className={`mj-num ${s.calc}`}>
                      {e.lines.map((l) => (
                        <li key={l.key}>
                          <span className={s.k}>{l.name}</span>
                          <span className={s.v}>{l.unit_price != null ? `${numText(l.unit_price)}원 × ${decText(l.qty)}${l.unit} = ${numText(l.amount)}원` : `${l.included_in_name ?? "다른 줄"}에 포함`}</span>
                          {l.source ? <span className={s.f}>{l.source}</span> : null}
                        </li>
                      ))}
                    </ul>
                    <p className={`mj-num ${s.sum}`}>
                      {e.exempt ? `공급가 ${wonFull(e.supply)} · 면세 업체라 부가세 없음 = ${wonFull(e.total)}` : `공급가 ${wonFull(e.supply)} + 부가세 ${wonFull(e.vat)} = ${wonFull(e.total)}`}
                      <span className={s.by}> · 믿집 계산</span>
                    </p>
                    {ni.length ? <p className="mj-small">{ni.slice(0, -1).concat(eunNeun(ni[ni.length - 1])).join(" · ")} 집을 봐야 알 수 있어 빠져 있어요.</p> : null}
                  </div>
                </details>
              </div>
            ) : (
              <div>
                <p className={s.none}>가견적 없음</p>
                <p className="mj-small">실측 뒤 금액</p>
              </div>
            )}
          </div>
        </section>

        <section className={`mj-sec ${s.sec}`} aria-labelledby="pt-t">
          <div className="mj-sec-l">
            <h2 id="pt-t" className="mj-h2">시공 사례</h2>
            {pr.portfolio.length ? <p className="mj-small">사진 {pr.portfolio.length}장 · 사진의 위치 정보는 지웠어요.</p> : null}
          </div>
          <div className="mj-sec-c">
            {pr.portfolio.length ? (
              <ul role="list" className={s.ph}>
                {pr.portfolio.map((ph) => {
                  const cap = [ph.space, ph.title].filter(Boolean).join(" · ");
                  const sub = [ph.pyeong ? `아파트 ${ph.pyeong}평` : "", ph.taken_on ? dotDate(ph.taken_on).slice(0, 7) : ""].filter(Boolean).join(" · ");
                  return (
                    <li key={ph.id}>
                      <figure>
                        <div className={s.pic}>
                          {/* 업체 사진은 API가 권한을 확인하고 내준다(이 집에서 고를 수 있는 업체만) */}
                          <img src={photoSrc(ph.file_id)} alt={`${cap} 시공 사진`} loading="lazy" />
                        </div>
                        <figcaption>{cap}{sub ? <><br /><span className="mj-small mj-num">{sub}</span></> : null}</figcaption>
                      </figure>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="mj-small">아직 올린 시공 사진이 없어요.</p>}
          </div>
        </section>

        <section className={`mj-sec ${s.sec}`} aria-labelledby="fq-t">
          <div className="mj-sec-l"><h2 id="fq-t" className="mj-h2">자주 묻는 5가지</h2><p className="mj-small">{iGa(name)} 적은 답이에요.</p></div>
          <div className="mj-sec-c">
            <dl className={s.qa}>
              {FAQ_LABEL.map((f) => (
                <div key={f.key}><dt>{f.label}</dt><dd>{faqs.get(f.key) ?? <span className="mj-sub">적지 않았어요</span>}</dd></div>
              ))}
            </dl>
          </div>
        </section>

        <section className={`mj-sec ${s.sec}`} aria-labelledby="bz-t">
          <div className="mj-sec-l"><h2 id="bz-t" className="mj-h2">사업자 · 자격 정보</h2></div>
          <div className="mj-sec-c">
            <details className={s.dtl}>
              <summary><Chev className={s.chev} /><span className="mj-num">{biz.length}</span>개 항목 보기</summary>
              <ul className={`mj-num ${s.bz}`}>
                {biz.map((b) => <li key={b.k}><span className={s.bk}>{b.k}</span><span className={s.bv}>{b.v}</span><Mark kind={b.mark} /></li>)}
              </ul>
              <p className={`mj-small ${s.bzNote}`}>&lsquo;업체가 적음&rsquo;은 믿집이 확인하지 않은 값이에요.</p>
            </details>
          </div>
        </section>

        <div className={s.end}>
          <div className={s.endRow}>
            {live ? <VisitState status={live.status} /> : deal ? null : req("rq-end")}
            <Link className={s.gbtn} href={listHref}>업체 목록으로</Link>
          </div>
          <p className="mj-small mj-num">{pr.updated_at ? `업체 정보 최종 수정 ${daySeoul(pr.updated_at)} · ` : ""}믿집은 공사 계약 당사자가 아니에요. 계약은 업체와 직접 해요.</p>
        </div>
      </div>
    </main>
  );
}
