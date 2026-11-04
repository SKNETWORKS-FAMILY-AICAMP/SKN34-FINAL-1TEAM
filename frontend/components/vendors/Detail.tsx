// 업체 자세히 조각 (시안 WVendorDetail · WVendorsPanel). 전체 화면(/vendors/[vid])과 업체 고르기 오른쪽 창이 같이 쓴다.
// 가견적 줄은 API(계산 엔진) 값을 그대로 보이고 화면에서 곱하거나 더하지 않는다.
// 고객이 보는 금액은 모두 부가세 포함이라 금액 옆에 따로 적지 않는다('어떻게 계산했나요'에서만 나눠 보인다).
import type { ProjectVendorDetail } from "@/lib/api/visit";
import { CONTACT_LABEL, bizNoText } from "@/lib/labels";
import { decText, dotDate, numText, won, wonFull } from "@/lib/format";
import { Check, Chev, Dash, Pen } from "./icons";
import { FAQ_LABEL, asMethod, callHours, day, eunNeun, feeText, iGa, vsText } from "./text";
import s from "./Detail.module.css";

type D = ProjectVendorDetail;

/** 사업자 확인함 (누르면 믿집이 확인한 것 · 확인하지 않은 것) */
export function Verified({ d, big = false }: { d: D; big?: boolean }) {
  return (
    <details className={s.dtl}>
      <summary>
        <span className={`mj-st mj-st-ok ${big ? s.big : ""}`}><Check size={big ? 16 : 14} />사업자 확인함{d.verified_on ? <span className="mj-num"> · {dotDate(d.verified_on)}</span> : null}</span>
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
  );
}

/** 실측 비용 · 출장 지역 · 연락 방법 · 연락 받는 시간 */
export function ContactFacts({ d }: { d: D }) {
  const pr = d.profile;
  const m = asMethod(pr.contact_method);
  const hours = callHours(pr.call_start, pr.call_end);
  return (
    <dl className={s.kv}>
      <dt>실측 비용</dt><dd className={s.strong}>{feeText(pr.visit_fee)}</dd>
      <dt>출장 지역</dt><dd>{eunNeun(d.area_name)} 출장 지역 {d.in_area ? "안" : "밖"}이에요</dd>
      {m ? <><dt>연락 방법</dt><dd>{CONTACT_LABEL[m]}</dd></> : null}
      {hours ? <><dt>연락 받는 시간</dt><dd className="mj-num">{hours}</dd></> : null}
    </dl>
  );
}

/** 가견적 금액 · 표준견적서 대비, 접힌 '어떻게 계산했나요' */
export function Estimate({ d, name, big = false }: { d: D; name: string; big?: boolean }) {
  const e = d.estimate;
  if (!e) {
    return (
      <div>
        <p className={s.none}>가견적 없음</p>
        <p className="mj-small">실측 뒤 금액</p>
      </div>
    );
  }
  const vs = vsText(e.vs_standard, e.vs_direction);
  const ni = d.not_included;
  return (
    <div className={s.est}>
      <p className={`mj-num ${big ? s.totalBig : s.total}`}>{won(e.total)}</p>
      {vs ? <p className="mj-small mj-num">{vs}</p> : null}
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
  );
}

/** 시공 사진. max가 있으면 앞에서 그만큼만 */
export function Photos({ d, projectId, max }: { d: D; projectId: string; max?: number }) {
  const all = d.profile.portfolio;
  if (!all.length) return <p className="mj-small">아직 올린 시공 사진이 없어요.</p>;
  const items = max ? all.slice(0, max) : all;
  // 업체 사진은 API가 권한을 확인하고 내준다(이 집에서 고를 수 있는 업체만)
  const src = (fileId: string) => `/api/projects/${projectId}/vendors/${d.profile.vendor_id}/photos/${fileId}`;
  return (
    <ul role="list" className={s.ph}>
      {items.map((ph) => {
        const cap = [ph.space, ph.title].filter(Boolean).join(" · ");
        const sub = [ph.pyeong ? `아파트 ${ph.pyeong}평` : "", ph.taken_on ? dotDate(ph.taken_on).slice(0, 7) : ""].filter(Boolean).join(" · ");
        return (
          <li key={ph.id}>
            <figure>
              <div className={s.pic}><img src={src(ph.file_id)} alt={`${cap} 시공 사진`} loading="lazy" /></div>
              <figcaption>{cap}{sub ? <><br /><span className="mj-small mj-num">{sub}</span></> : null}</figcaption>
            </figure>
          </li>
        );
      })}
    </ul>
  );
}

/** 자주 묻는 5가지. fold면 질문만 보이고 누르면 답(오른쪽 창) */
export function Faqs({ d, fold = false }: { d: D; fold?: boolean }) {
  const faqs = new Map(d.profile.faqs.map((f) => [f.key, f.answer]));
  const none = <span className="mj-sub">적지 않았어요</span>;
  if (fold) {
    return (
      <div className={s.fold}>
        {FAQ_LABEL.map((f) => (
          <details key={f.key} className={s.dtl}>
            <summary><Chev className={s.chev} />{f.label}</summary>
            <p className={s.ans}>{faqs.get(f.key) ?? none}</p>
          </details>
        ))}
      </div>
    );
  }
  return (
    <dl className={s.qa}>
      {FAQ_LABEL.map((f) => <div key={f.key}><dt>{f.label}</dt><dd>{faqs.get(f.key) ?? none}</dd></div>)}
    </dl>
  );
}

function Mark({ kind }: { kind: "ok" | "self" | "none" }) {
  if (kind === "ok") return <span className="mj-st mj-st-ok"><Check />믿집 확인</span>;
  if (kind === "self") return <span className="mj-st mj-st-off"><Pen />업체가 적음</span>;
  return <span className="mj-st mj-st-wait"><Dash />확인 안 됨</span>;
}

/** 사업자 · 자격 정보 (접힘) */
export function BizInfo({ d, label }: { d: D; label: React.ReactNode }) {
  const pr = d.profile;
  const name = pr.biz_name ?? "업체";
  const opened = pr.opened_on ? `${pr.opened_on.slice(0, 7).replace("-", ".")}${d.years_open != null ? ` · 업력 ${d.years_open}년` : ""}` : null;
  const biz: { k: string; v: string; mark: "ok" | "self" | "none" }[] = [
    { k: "상호 · 대표자", v: [name, pr.owner_name].filter(Boolean).join(" · "), mark: "ok" },
    ...(pr.biz_no ? [{ k: "사업자등록번호", v: bizNoText(pr.biz_no), mark: "ok" as const }] : []),
    ...(opened ? [{ k: "개업일", v: opened, mark: "ok" as const }] : []),
    ...(pr.address_area ? [{ k: "사업장", v: pr.address_area, mark: "ok" as const }] : []),
    { k: "실내건축공사업 등록", v: pr.interior_license ?? "적지 않았어요", mark: pr.interior_license ? "self" : "none" },
    { k: "보험", v: pr.insurance ?? "적지 않았어요", mark: pr.insurance ? "self" : "none" },
  ];
  return (
    <details className={s.dtl}>
      <summary><Chev className={s.chev} />{label}<span className="mj-small mj-num"> {biz.length}개</span></summary>
      <ul className={`mj-num ${s.bz}`}>
        {biz.map((b) => <li key={b.k}><span className={s.bk}>{b.k}</span><span className={s.bv}>{b.v}</span><Mark kind={b.mark} /></li>)}
      </ul>
      <p className={`mj-small ${s.bzNote}`}>&lsquo;업체가 적음&rsquo;은 믿집이 확인하지 않은 값이에요.</p>
    </details>
  );
}
