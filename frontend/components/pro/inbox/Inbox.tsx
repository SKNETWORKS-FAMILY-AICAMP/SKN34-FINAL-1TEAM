// 작업함 (시안 WVInbox): 탭(할 일 · 기다림 · 공사 · 끝남) + 고른 탭의 표 + 오른쪽 카드 3개.
// 탭은 주소(?tab=)로 바꾼다. 수는 GET /vendor/visits/summary, 줄은 GET /vendor/visits?tab=.
// 공사 탭은 업체 확정이 생기는 뒤에 채운다(지금은 늘 0). 견적서 올리기 · 보낸 견적서 보기는 실측 견적서 기능에서 붙인다.
import Link from "next/link";
import type { InboxPage, InboxSummary } from "@/lib/api/visit";
import type { VendorPrices, VendorProfile } from "@/lib/api/vendor";
import { dateSeoul, md } from "@/lib/format";
import { phoneText, tradeLabel } from "@/lib/labels";
import { contactLine, type Method } from "@/components/pro/profile/text";
import { Dash, Who } from "./Cells";
import { DeclineButton } from "./DeclineButton";
import { DoneNote } from "./DoneNote";
import { DoneTable } from "./DoneTable";
import { EMPTY, TABS, TAB_NOTE, dayOf, pyeongText, whoOf, type Row, type Tab } from "./text";
import s from "./inbox.module.css";

type Props = {
  tab: Tab;
  summary: InboxSummary;
  page: InboxPage | null;
  prices: { trade: string; p: VendorPrices | null }[];
  profile: VendorProfile | null;
  /** 프로필을 못 읽었을 때 쓰는 가입 신청의 대표자 · 휴대폰 · 상호 */
  fallback: { owner: string | null; phone: string | null; biz: string | null };
  note: string | null;
};

function TodoRow({ v, vendorName }: { v: Row; vendorName: string }) {
  const call = v.new && !!v.customer;
  return (
    <tr>
      <td className={`mj-num ${s.cx} ${s.day}`}>{dayOf(v.received_at)}</td>
      <th scope="row"><Who v={v} /></th>
      <td className={s.cx}>{v.region}</td>
      <td className="r mj-num">{pyeongText(v.pyeong)}</td>
      <td>
        {v.new && v.customer ? <><span className="mj-st mj-st-now">새 요청 · 연락처 보냄</span><br /><span className={s.b}>고객에게 연락하기</span></>
          : v.new ? <><span className="mj-st mj-st-now">새 요청</span><br /><span className={s.b}>고객이 먼저 연락해요</span><br /><span className="mj-small">고객이 이 요청 번호를 말해요</span></>
          : <span className={s.b}>견적서 올리기</span>}
      </td>
      <td className="r">
        <div className={s.acts}>
          {call ? (
            <Link className={`mj-btn mj-btn-pri mj-btn-sm ${s.go} ${s.goPri}`} href={`/vendor/requests/${v.id}`} aria-label={`${whoOf(v)} 요청 보고 연락하기`}>연락하기</Link>
          ) : (
            <Link className={`mj-btn mj-btn-sec mj-btn-sm ${s.go}`} href={`/vendor/requests/${v.id}`} aria-label={`${whoOf(v)} 요청 보기`}>요청 보기</Link>
          )}
          <DeclineButton visitId={v.id} name={v.customer?.name ?? null} code={v.code} vendorName={vendorName} />
        </div>
      </td>
    </tr>
  );
}

function WaitRow({ v }: { v: Row }) {
  return (
    <tr>
      <td className={`mj-num ${s.cx} ${s.day}`}>{dayOf(v.received_at)}</td>
      <th scope="row"><Who v={v} /></th>
      <td className={s.cx}>{v.region}</td>
      <td className="r mj-num">{pyeongText(v.pyeong)}</td>
      <td><span className="mj-st mj-st-off"><Dash />실측 견적서 보냄</span><br /><span className="mj-small">고객 결정 기다림</span></td>
      <td className="r mj-num">{v.waiting_days ? `${v.waiting_days}일째` : "오늘"}</td>
      <td className="r"><Link className={`mj-lnk ${s.go}`} href={`/vendor/requests/${v.id}`} aria-label={`${whoOf(v)} 요청 보기`}>요청 보기</Link></td>
    </tr>
  );
}

function Table({ name, caption, head, children }: { name: string; caption: string; head: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mj-table-box" role="region" tabIndex={0} aria-label={`${name} 표, 옆으로 넘겨 볼 수 있어요`}>
      <table className={`mj-table ${s.dense} ${s.cards}`}>
        <caption className="mj-sr">{caption}</caption>
        <thead><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** 가견적에 쓸 견적서 한 공정: 파일 수 · 확인한 단가 줄 수 · 확인한 날 */
function PriceLine({ trade, p, many }: { trade: string; p: VendorPrices | null; many: boolean }) {
  const pre = many ? `${tradeLabel(trade)} · ` : "";
  const files = p?.files.length ?? 0;
  const conf = p?.confirmed;
  if (!conf) {
    return (
      <>
        <p className="mj-num">{pre}{files ? `파일 ${files}개 · 확인한 단가 없음` : "올린 견적서가 없어요"}</p>
        <p className="mj-small">고객 목록에 &lsquo;가견적 없음 · 실측 뒤 금액&rsquo;으로 보여요</p>
      </>
    );
  }
  const lines = conf.lines.filter((l) => l.unit_price != null).length;
  return (
    <>
      <p className="mj-num">{pre}파일 {files}개 · 확인한 단가 {lines}줄</p>
      <p className="mj-small mj-num">{conf.confirmed_at ? `${md(dateSeoul(conf.confirmed_at))}에 확인했어요 · ` : ""}고객 목록에 가견적이 보여요</p>
    </>
  );
}

export function Inbox({ tab, summary, page, prices, profile, fallback, note }: Props) {
  const count: Record<Tab, number> = { todo: summary.todo, waiting: summary.waiting, work: summary.jobs, done: summary.done };
  const items = page?.items ?? [];
  const label = TABS.find((t) => t.k === tab)?.t ?? "";
  const vendorName = profile?.biz_name ?? fallback.biz ?? "우리 업체";
  const owner = profile?.owner_name ?? fallback.owner;
  const phone = profile?.phone ?? fallback.phone;
  const way = profile?.contact_method
    ? contactLine(profile.contact_method as Method, profile.call_start?.slice(0, 5) ?? "", profile.call_end?.slice(0, 5) ?? "", profile.visit_fee ?? 0, profile.visit_fee_refund)
    : "실측 방식을 아직 정하지 않았어요.";
  const r = summary.recent;

  let body: React.ReactNode;
  if (tab === "work" || !items.length) body = <p className={s.empty}>{EMPTY[tab]}</p>;
  else if (tab === "todo") {
    body = (
      <Table name="할 일" caption={`할 일 ${items.length}건, 새 요청부터`} head={<>
        <th scope="col" className={s.cx}>받은 날</th><th scope="col">고객 · 요청</th><th scope="col" className={s.cx}>지역</th>
        <th scope="col" className="r">평형</th><th scope="col">다음 할 일</th><th scope="col"><span className="mj-sr">행동</span></th>
      </>}>
        {items.map((v) => <TodoRow key={v.id} v={v} vendorName={vendorName} />)}
      </Table>
    );
  } else if (tab === "waiting") {
    body = (
      <Table name="기다림" caption={`고객을 기다리는 일 ${items.length}건, 오래 기다린 순서`} head={<>
        <th scope="col" className={s.cx}>받은 날</th><th scope="col">고객</th><th scope="col" className={s.cx}>지역</th>
        <th scope="col" className="r">평형</th><th scope="col">지금</th><th scope="col" className="r">기다린 날</th><th scope="col"><span className="mj-sr">행동</span></th>
      </>}>
        {items.map((v) => <WaitRow key={v.id} v={v} />)}
      </Table>
    );
  } else body = <DoneTable items={items} next={page?.next_before ?? null} total={summary.done} />;

  return (
    <main id="main" className={`mj-wrap ${s.page}`}>
      {note ? <DoneNote text={note} /> : null}
      <h1 className="mj-h1">작업함</h1>

      <div className={s.cols}>
        <div className={s.main}>
          <nav className={`mj-tabs ${s.tabs}`} aria-label="작업함 나누기">
            {TABS.map((t) => (
              <Link key={t.k} href={t.k === "todo" ? "/vendor" : `/vendor?tab=${t.k}`} aria-current={t.k === tab ? "page" : undefined}>
                {t.t} <span className="mj-num">{count[t.k]}</span><span className="mj-sr">건</span>
              </Link>
            ))}
          </nav>
          <section id={tab} aria-labelledby="tab-t" className={s.sec}>
            <h2 id="tab-t" className="mj-sr">{label} {count[tab]}건</h2>
            {TAB_NOTE[tab] ? <p className="mj-small">{TAB_NOTE[tab]}</p> : null}
            {body}
          </section>
        </div>

        <aside aria-label="가견적 · 연락처 · 최근 30일" className={s.aside}>
          <section aria-labelledby="price-t" className={`${s.card} ${s.cardFirst}`}>
            <div className={s.cardHead}>
              <h2 id="price-t" className="mj-h3">가견적에 쓸 견적서</h2>
              <Link className="mj-lnk" href="/vendor/profile#prices" aria-label="가견적에 쓸 견적서 고치기">고치기</Link>
            </div>
            {prices.map((x) => <PriceLine key={x.trade} trade={x.trade} p={x.p} many={prices.length > 1} />)}
          </section>

          <section aria-labelledby="tel-t" className={s.card}>
            <div className={s.cardHead}>
              <h2 id="tel-t" className="mj-h3">연락처 · 실측 방식</h2>
              <Link className="mj-lnk" href="/vendor/profile#visit" aria-label="연락처 · 실측 방식 바꾸기">바꾸기</Link>
            </div>
            <p className={`mj-num ${s.telNo}`}>{[owner, phoneText(phone)].filter(Boolean).join(" · ") || "—"}</p>
            <p>{way}</p>
            <p className="mj-small">실측을 요청한 고객에게 보이고, 요청이 닫히면 가려져요. 확정한 고객에게는 공사가 끝난 날부터 1년까지 보여요.</p>
          </section>

          <section aria-labelledby="mon-t" className={s.card}>
            <h2 id="mon-t" className="mj-h3">최근 30일</h2>
            <dl className={`mj-num ${s.kv}`}>
              <div><dt>실측 요청</dt><dd><span className={s.big}>{r.requested}</span>건</dd></div>
              <div><dt>실측 견적서 보냄</dt><dd><span className={s.big}>{r.quoted}</span>건</dd></div>
              <div><dt>확정</dt><dd><span className={s.big}>{r.chosen}</span>건</dd></div>
            </dl>
          </section>
        </aside>
      </div>
    </main>
  );
}
