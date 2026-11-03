"use client";
// 업체 고르기 목록 + 실측 요청에 담기 (시안 WVendors). 담은 업체 · 남길 말은 이 기기 sessionStorage에 프로젝트 · 공정마다 둔다.
// 금액 · 퍼센트는 API 값을 그대로 보인다. 담을 수 있는 수(slotsLeft)는 서버가 정한다(공정마다 3곳까지).
import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CONTACT_LABEL, tradeLabel } from "@/lib/labels";
import { dotDate, won } from "@/lib/format";
import { Check, Chev, Close, Picked, Plus, VisitState } from "./icons";
import { readPick, writePick } from "./pick";
import { RequestDialog } from "./RequestDialog";
import { FAILED_TEXT, areasText, asMethod, declineText, feeText, isLive, vsText, when, type Created, type VendorRow, type VTrade } from "./text";
import s from "./VendorBoard.module.css";

type Props = {
  /** 목록 위: 공정 탭 · 표준견적서 합계 (서버가 그린다) */
  head: ReactNode;
  projectId: string;
  trade: VTrade;
  areaName: string;
  slotsLeft: number;
  consentVer: string;
  vendors: VendorRow[];
  /** 닫힌 요청이 닫힌 때 (visit id → 시각, 실측 요청 목록에서) */
  closedAt: Record<string, string>;
};

type Sent = { ok: string[]; failed: { name: string; code: string }[] };

function Estimate({ v }: { v: VendorRow }) {
  if (v.estimate_total == null) {
    return <><p className={s.none}>가견적 없음</p><p className="mj-small">실측 뒤 금액</p></>;
  }
  const vs = vsText(v.vs_standard, v.vs_direction);
  return (
    <>
      <p className={`mj-num ${s.money}`}><span className={s.total}>{won(v.estimate_total)}</span><span className="mj-small">부가세 포함</span></p>
      <p className="mj-small">업체가 확인한 단가로 믿집이 계산했어요</p>
      {vs ? <p className={`mj-num ${s.vs}`}>{vs} {v.vs_direction === "same" ? null : <span aria-hidden="true">{v.vs_direction === "less" ? "▼" : "▲"}</span>}</p> : null}
    </>
  );
}

export function VendorBoard({ head, projectId, trade, areaName, slotsLeft, consentVer, vendors, closedAt }: Props) {
  const router = useRouter();
  const [ids, setIds] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [onlyEst, setOnlyEst] = useState(false);
  const [open, setOpen] = useState(false);
  const [sent, setSent] = useState<Sent | null>(null);
  const sentRef = useRef<HTMLDivElement>(null);
  const tName = tradeLabel(trade);
  const base = `/projects/${projectId}`;

  useEffect(() => {
    const p = readPick(projectId, trade);
    setIds(p.ids);
    setMsg(p.msg);
  }, [projectId, trade]);
  useEffect(() => { if (sent) sentRef.current?.focus(); }, [sent]);

  const byId = new Map(vendors.map((v) => [v.vendor_id, v]));
  const live = vendors.filter((v) => v.visit && isLive(v.visit.status));
  // 담은 것: 목록에 있고 아직 열린 요청이 없는 업체만, 남은 칸까지
  const picked = ids.filter((id) => { const v = byId.get(id); return v && !(v.visit && isLive(v.visit.status)); }).slice(0, Math.max(0, slotsLeft));
  const room = Math.max(0, slotsLeft - picked.length);
  const shown = onlyEst ? vendors.filter((v) => v.estimate_total != null) : vendors;

  function save(next: string[], nextMsg = msg) {
    setIds(next);
    writePick(projectId, trade, { ids: next, msg: nextMsg });
  }
  function toggle(id: string) {
    setSent(null);
    if (picked.includes(id)) save(picked.filter((x) => x !== id));
    else if (room > 0) save([...picked, id]);
  }

  function onSent(r: Created) {
    setOpen(false);
    const done = new Set([...r.created.map((c) => c.vendor_id ?? ""), ...r.failed.map((f) => f.vendor_id)]);
    const rest = picked.filter((id) => !done.has(id));
    setMsg("");
    save(rest, "");
    setSent({
      ok: r.created.map((c) => c.vendor_name),
      failed: r.failed.map((f) => ({ name: byId.get(f.vendor_id)?.biz_name ?? "업체", code: f.code })),
    });
    router.refresh();
  }

  const roomText = slotsLeft <= 0
    ? "이미 3곳에 요청했어요. 요청을 취소하거나 업체가 못 받으면 칸이 비어요."
    : room === 0 ? "3곳까지 담았어요. 하나를 빼면 담을 수 있어요." : `${room}곳 더 담을 수 있어요 (3곳까지)`;
  const backTo = `${base}/vendors?trade=${trade}`;

  return (
    <div className={s.layout}>
      <div className={s.main}>
        {head}
        <div role="group" aria-label="목록 거르기" className={s.filter}>
          <p className={`mj-small ${s.area}`}>{areaName}에서 일하는 업체만 보여요 · 가견적 낮은 순</p>
          <label className={s.ck}><input type="checkbox" checked={onlyEst} onChange={(e) => setOnlyEst(e.target.checked)} />가견적이 있는 업체만</label>
        </div>

        <h2 className="mj-sr">{tName} 업체 {shown.length}곳</h2>
        <div className={`${s.vr} ${s.vh}`} aria-hidden="true"><span className={s.c1}>업체</span><span className={s.c2}>확인</span><span className={s.c3}>실측 · 연락</span><span className={s.c4}>가견적</span><span className={s.c5} /></div>
        {shown.length ? (
          <ul role="list" className={s.list}>
            {shown.map((v) => {
              const name = v.biz_name ?? "업체";
              const m = asMethod(v.contact_method);
              const vis = v.visit;
              const on = picked.includes(v.vendor_id);
              const full = !on && room === 0;
              return (
                <li key={v.vendor_id} className={s.vr}>
                  <div className={s.c1}>
                    <h3 className="mj-h3">{name}</h3>
                    <p className="mj-small">{[...v.trades.map(tradeLabel), areasText(v.areas, areaName)].join(" · ")}</p>
                    <Link className={`mj-lnk ${s.sm}`} href={`${base}/vendors/${v.vendor_id}?trade=${trade}`}>업체 자세히<span className="mj-sr">, {name}</span></Link>
                  </div>
                  <ul className={`${s.c2} ${s.facts}`} aria-label="믿집 확인">
                    <li><span className={s.okIcon}><Check /></span><span>사업자 확인함{v.verified_on ? <> <span className="mj-small mj-num">{dotDate(v.verified_on)}</span></> : null}</span></li>
                  </ul>
                  <div className={s.c3}>
                    <p className={s.mlab}>실측 · 연락</p>
                    <p>{feeText(v.visit_fee)}{m ? ` · ${CONTACT_LABEL[m]}` : ""}</p>
                    {v.visit_fee && v.visit_fee_refund ? <p className="mj-small">계약하면 빼 줘요</p> : null}
                  </div>
                  <div className={s.c4}>
                    <p className={s.mlab}>가견적</p>
                    <Estimate v={v} />
                  </div>
                  <div className={s.c5}>
                    {vis && isLive(vis.status) ? (
                      <div className={s.state}>
                        <VisitState status={vis.status} />
                        {vis.status === "requested" ? <Link className={`mj-lnk ${s.sm}`} href={`${base}/visits?trade=${trade}`}>연락처 보기<span className="mj-sr">, {name}</span></Link> : null}
                        {vis.status === "quoted" ? <Link className={`mj-lnk ${s.sm}`} href={`${base}/quotes?trade=${trade}`}>비교하기<span className="mj-sr">, {name} 실측 견적서</span></Link> : null}
                      </div>
                    ) : (
                      <>
                        {vis ? (
                          <div className={s.state}>
                            <VisitState status={vis.status} />
                            <span className="mj-small mj-num">{[declineText(vis.decline_code), when(closedAt[vis.id])].filter(Boolean).join(" · ")}</span>
                          </div>
                        ) : null}
                        <button type="button" className={s.pick} aria-pressed={on} aria-disabled={full || undefined} aria-describedby={full ? "pk-room" : undefined} onClick={() => toggle(v.vendor_id)}>
                          {on ? <Picked /> : <Plus />}실측 요청에 담기<span className="mj-sr">, {name}</span>
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className={s.empty}>가견적이 있는 업체가 아직 없어요. 거르기를 풀면 모든 업체가 보여요.</p>
        )}
      </div>

      <aside className={s.side} aria-labelledby="pk-t">
        {sent ? (
          <div ref={sentRef} tabIndex={-1} role="status" className={s.sent}>
            {sent.ok.length ? (
              <>
                <p className={s.sentLine}><span className="mj-st mj-st-ok"><Check />보냈어요</span><b>{sent.ok.join(" · ")}에 요청했어요. 업체에 먼저 연락해 주세요.</b></p>
                <Link className={`mj-btn mj-btn-pri ${s.self}`} href={`${base}/visits?trade=${trade}`}>연락하러 가기</Link>
              </>
            ) : null}
            {sent.failed.map((f) => (
              <p key={f.name + f.code} className={s.sentLine}>
                <span className="mj-st mj-st-bad"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3.5 3.5l7 7M10.5 3.5l-7 7" /></svg>못 보냈어요</span>
                <b>{f.name}</b><span className="mj-small">{FAILED_TEXT[f.code] ?? "요청하지 못했어요."}</span>
              </p>
            ))}
          </div>
        ) : null}

        <form className={s.form} onSubmit={(e) => { e.preventDefault(); if (picked.length) setOpen(true); }} noValidate>
          <h2 id="pk-t" className="mj-h3">{tName} 실측 요청할 업체 <span className="mj-num">{picked.length}<span className="mj-sr">곳</span></span></h2>
          {picked.length ? (
            <ul role="list" className={s.basket}>
              {picked.map((id) => {
                const v = byId.get(id)!;
                const name = v.biz_name ?? "업체";
                return (
                  <li key={id}>
                    <div className={s.bName}>
                      <p className={s.strong}>{name}</p>
                      <p className="mj-small mj-num">{v.estimate_total != null ? `가견적 ${won(v.estimate_total)} · 부가세 포함` : "가견적 없음 · 실측 뒤 금액"}</p>
                    </div>
                    <button type="button" className={s.rmv} aria-label={`${name} 빼기`} onClick={() => toggle(id)}><Close /></button>
                  </li>
                );
              })}
            </ul>
          ) : <p className={`mj-small ${s.basketEmpty}`}>아직 담은 업체가 없어요. 업체 줄의 &lsquo;실측 요청에 담기&rsquo;를 눌러 주세요.</p>}
          <p id="pk-room" className="mj-hint">{roomText}</p>
          {live.length ? <p className="mj-small">이미 요청한 업체 {live.length}곳 · <Link className={`mj-lnk ${s.sm}`} href={`${base}/visits?trade=${trade}`}>연락하러 가기</Link></p> : null}
          <div className="mj-field">
            <label className="mj-label" htmlFor="pk-memo">업체에 남길 말 <span className="mj-opt">(선택)</span></label>
            <textarea id="pk-memo" className="mj-textarea" rows={3} maxLength={300} value={msg} aria-describedby="pk-memo-h" placeholder="예: 평일 오후나 주말 오전이 좋아요." onChange={(e) => { setMsg(e.target.value); writePick(projectId, trade, { ids: picked, msg: e.target.value }); }} />
            <p id="pk-memo-h" className="mj-hint mj-num">{msg.length}/300자 · 담은 업체 모두에게 가요</p>
          </div>
          <p id="pk-share" className={s.share}>요청하면 업체 연락처가 보여요. 내 연락처는 보내기로 고를 때만 가요.</p>
          <button type="submit" className="mj-btn mj-btn-pri" aria-haspopup="dialog" aria-describedby="pk-share" disabled={!picked.length}>실측 요청 보내기</button>
          <details className={s.dtl}>
            <summary><Chev className={s.chev} />가견적은 어떻게 계산해요?</summary>
            <div className={s.dtlBody}>
              <p>업체가 직접 넣고 확인한 단가만 써요. 이 집 수량(표준견적서와 같아요)에 업체 단가를 곱하고 부가세를 더해요. 실측 뒤 금액인 줄은 빠져 있어요.</p>
              <p>줄마다 어떻게 계산했는지는 &lsquo;업체 자세히&rsquo;에서 볼 수 있어요.</p>
              <p className="mj-sub">단가를 확인하지 않은 업체는 &lsquo;가견적 없음&rsquo;이에요.</p>
            </div>
          </details>
        </form>
      </aside>

      <RequestDialog
        open={open}
        onClose={() => setOpen(false)}
        onSent={onSent}
        projectId={projectId}
        trade={trade}
        vendors={picked.map((id) => ({ id, name: byId.get(id)?.biz_name ?? "업체" }))}
        message={msg}
        consentVer={consentVer}
        backTo={backTo}
        idp="rq"
      />
    </div>
  );
}
