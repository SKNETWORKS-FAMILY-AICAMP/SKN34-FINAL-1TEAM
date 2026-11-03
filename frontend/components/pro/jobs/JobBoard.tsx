"use client";
// 공사 날짜 내기 · 다시 내기 · 고객 요청에 답하기 (시안 WVCalendar '공사 날짜 내기').
// 날짜 · 인원을 바꾸면 잠깐 쉬었다가(600ms) GET .../dates/preview로 일하는 날 · 필요한 기간 · 겹침을 받아 보인다(화면에서 세지 않음).
// '공사 날짜 내기' → 확인 창 → POST .../dates. 열린 요청이 있으면 그 요청 id를 함께 보내고, 그사이 바뀌었으면(409 request_changed) 다시 읽는다.
// 고객이 수락했거나(scheduled · change_requested · change_proposed) 다시 골랐으면(closed) 쓰기를 막고, 수락한 날짜만 보인다.
// 수락한 뒤의 일(날짜 바꾸기 kind=change · 추가 금액 · 공사 끝냄)은 22번 화면에서 붙인다.
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FieldError } from "@/components/auth/parts";
import { Alert, Dash } from "@/components/pro/inbox/Cells";
import { CopyPhone } from "@/components/pro/inbox/CopyPhone";
import { DoneNote } from "@/components/pro/inbox/DoneNote";
import { iGa, pyeongText, telHref, workText } from "@/components/pro/inbox/text";
import { ApiError, call, errorText } from "@/lib/backend/client";
import { dateSeoul, md, won } from "@/lib/format";
import { crewLabel, phoneText, tradeLabel } from "@/lib/labels";
import { FitChart } from "./FitChart";
import { JobDateField } from "./JobDateField";
import { CREW_MAX, METHOD_LABEL, RESELECTED, eunNeun, isOpen, methodText, needText, span, stamp, type Job, type Method, type Preview } from "./text";
import s from "./jobs.module.css";

type Fields = { method: Method; call_on: string; start: string; end: string; crew: string };
type Key = "call_on" | "start" | "end" | "dates" | "crew";
type Errs = Partial<Record<Key, string>>;

const ORDER: Key[] = ["call_on", "start", "end", "dates", "crew"];
const FOCUS: Record<Key, string> = { call_on: "j-call", start: "j-start", end: "j-end", dates: "j-start", crew: "j-crew" };
const CREW_TEXT = `작업 인원은 1명부터 ${CREW_MAX}명까지 넣어 주세요.`;
const METHODS: Method[] = ["app", "call"];

/** 대기 날짜가 있으면 그 값으로 채운다. 고객이 다른 날짜를 요청했으면 날짜는 비우고 고객이 고른 시작일에서 고르게 한다 */
function fieldsOf(j: Job): Fields {
  const p = j.proposal;
  const ask = j.dates_state === "requested";
  return {
    method: (p?.method as Method | undefined) ?? "app",
    call_on: p?.call_on ?? "",
    start: ask ? "" : p?.start ?? "",
    end: ask ? "" : p?.end ?? "",
    crew: String(p?.crew ?? j.need.crew),
  };
}

const crewOk = (c: string) => /^\d{1,2}$/.test(c) && Number(c) >= 1 && Number(c) <= CREW_MAX;

/** API 오류 → 칸별 문장. 서버 문장(code = 칸 이름)은 그대로, 입력 검사(fields)는 한국어 문장으로 바꾼다 */
function errsOf(e: ApiError): Errs | null {
  const out: Errs = {};
  if (e.fields?.length) {
    for (const x of e.fields) {
      const k = String(x.loc[x.loc.length - 1] ?? "");
      if (k === "crew") out.crew = CREW_TEXT;
      else if (k === "start" || k === "end" || k === "call_on") out[k] = "날짜를 다시 골라 주세요.";
    }
  } else if (e.code === "off_day") out.dates = e.message;
  else if (e.code === "start" || e.code === "end" || e.code === "call_on") out[e.code] = e.message;
  return Object.keys(out).length ? out : null;
}

function focusFirst(e: Errs) {
  const k = ORDER.find((x) => e[x]);
  if (k) setTimeout(() => document.getElementById(FOCUS[k])?.focus(), 0);
}

const Check = () => <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M2.5 7.5l3 3 6-7" /></svg>;
const Phone = () => <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" className={s.ico}><path d="M3 1.8h2.2l1 2.7-1.4 1a7 7 0 003.7 3.7l1-1.4 2.7 1v2.2a1 1 0 01-1 1A10.2 10.2 0 012 2.8a1 1 0 011-1z" strokeLinejoin="round" /></svg>;

/** 머리 오른쪽 기한: 날짜를 아직 안 냈으면 확정 기한, 다른 날짜 요청이 열려 있으면 그 요청의 기한 (API 값) */
function Due({ job }: { job: Job }) {
  const st = job.dates_state;
  const r = job.request;
  const due = st === "waiting" ? job.dates_due_at : st === "requested" && r ? r.due_at : null;
  if (!due) return null;
  const late = st === "waiting" ? job.overdue : !!r?.overdue;
  return (
    <div className={s.due}>
      {late ? <p className="mj-st mj-st-bad"><Alert />기한 지남</p> : null}
      <p className="mj-num"><b>{stamp(due)}까지{late ? "였어요" : ""}</b> <span className="mj-small">{st === "waiting" ? "공사 날짜 낼 기한" : "다시 낼 기한"}</span></p>
      {late ? <p className="mj-small">자동으로 취소되지 않아요. 고객은 더 기다리거나 전화로 물어봐요.</p> : null}
    </div>
  );
}

type Props = { initial: Job; today: string };

export function JobBoard({ initial, today }: Props) {
  const router = useRouter();
  const [job, setJob] = useState(initial);
  const [f, setF] = useState<Fields>(() => fieldsOf(initial));
  const [errs, setErrs] = useState<Errs>({});
  const [pv, setPv] = useState<{ key: string; out: Preview } | null>(null);
  const [pvFail, setPvFail] = useState<{ key: string; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [sendErr, setSendErr] = useState("");
  const [lock, setLock] = useState<string | null>(null); // 409 invalid_state (수락함 · 다시 고름) → 서버 문장
  const [warn, setWarn] = useState<string | null>(null); // 409 request_changed · stale → 서버 문장, 다시 읽음
  const [note, setNote] = useState<string | null>(null); // 낸 뒤 맨 위 한 줄
  const seq = useRef(0);
  const sent = useRef(false); // 방금 보내서 확인 창을 닫음 (초점은 안내 줄로)
  const dlg = useRef<HTMLDialogElement>(null);

  const id = job.deal_id;
  const st = job.dates_state;
  const open = isOpen(st) && !lock;
  const v = job.visit;
  const c = v.customer;
  const q = job.quote;
  const p = job.proposal;
  const r = st === "requested" ? job.request : null;
  const work = workText(v);
  const trade = tradeLabel(job.trade);
  const holidays = useMemo(() => new Set(job.holidays.map((h) => h.day)), [job.holidays]);
  const weekendOff = !job.weekend_work;
  const whoSay = c?.name ? `${c.name} 님` : "고객";

  // 미리 계산: 시작일 · 끝나는 날 · 인원이 다 있을 때만, 마지막 값의 응답만 쓴다
  const key = `${f.start}|${f.end}|${f.crew}`;
  const ready = open && !!f.start && !!f.end && f.end >= f.start && crewOk(f.crew);
  useEffect(() => {
    if (!ready) return;
    const my = ++seq.current;
    const t = setTimeout(async () => {
      const qs = new URLSearchParams({ start: f.start, end: f.end, crew: f.crew });
      try {
        const out = await call<Preview>("GET", `/vendor/jobs/${id}/dates/preview?${qs.toString()}`);
        if (seq.current !== my) return;
        setPv({ key: `${f.start}|${f.end}|${f.crew}`, out });
        setPvFail(null);
        setErrs((m) => { const n = { ...m }; delete n.start; delete n.end; delete n.dates; delete n.crew; return n; });
      } catch (e) {
        if (seq.current !== my) return;
        const fe = e instanceof ApiError && e.status === 422 ? errsOf(e) : null;
        if (fe) setErrs((m) => ({ ...m, ...fe }));
        setPvFail({ key: `${f.start}|${f.end}|${f.crew}`, msg: fe ? "" : errorText(e) });
      }
    }, 600);
    return () => clearTimeout(t);
  }, [ready, id, f.start, f.end, f.crew]);

  const shown = pv && pv.key === key ? pv.out : null;
  const calc = ready && !shown && pvFail?.key !== key;
  const need = shown?.need ?? (Number(f.crew) === job.need.crew ? job.need : null);
  const clash = !!shown && shown.fit.conflicts.length > 0;

  function change(x: Partial<Fields>) {
    if (!open) return;
    setF((old) => {
      const next = { ...old, ...x };
      if (x.start !== undefined && next.end && next.start && next.end < next.start) next.end = ""; // 끝나는 날은 시작일부터
      return next;
    });
    setErrs((m) => { const n = { ...m }; for (const k of Object.keys(x)) delete n[k as Key]; if (x.start !== undefined || x.end !== undefined) delete n.dates; return n; });
    setNote(null);
  }

  function reset(out: Job) {
    setJob(out);
    setF(fieldsOf(out));
    setErrs({});
  }

  async function reload(fields: boolean) {
    try {
      const out = await call<Job>("GET", `/vendor/jobs/${id}`);
      if (fields) reset(out);
      else setJob(out);
    } catch {
      // 다시 읽지 못하면 화면을 새로 그린다
    }
    router.refresh();
  }

  /** '공사 날짜 내기': 빠진 칸이 있으면 그 칸으로, 없으면 확인 창 */
  function ask() {
    if (busy || !open) return;
    const miss: Errs = {};
    if (f.method === "call" && !f.call_on) miss.call_on = "통화한 날을 골라 주세요.";
    if (!f.start) miss.start = "시작일을 골라 주세요.";
    if (!f.end) miss.end = "끝나는 날을 골라 주세요.";
    if (!crewOk(f.crew)) miss.crew = CREW_TEXT;
    const bad = { ...errs, ...miss };
    if (Object.keys(bad).length) { setErrs(bad); focusFirst(bad); return; }
    setSendErr("");
    dlg.current?.showModal();
  }

  async function send() {
    if (busy) return;
    setBusy(true); setSendErr("");
    const before = st;
    try {
      const out = await call<Job>("POST", `/vendor/jobs/${id}/dates`, {
        start: f.start, end: f.end, crew: Number(f.crew), method: f.method,
        kind: "initial", // 수락 전 내기 · 다시 내기 (수락한 뒤 바꾸기 change는 22번 화면)
        call_on: f.method === "call" ? f.call_on : null,
        request_id: job.request?.id ?? null,
      });
      sent.current = true;
      dlg.current?.close();
      reset(out);
      setWarn(null);
      setNote(before === "requested" ? "고객 요청에 새 날짜로 답했어요. 고객이 수락하면 알림으로 알려 드려요."
        : before === "proposed" ? "공사 날짜를 다시 냈어요. 고객에게 ‘날짜가 바뀌었어요’로 보여요."
        : "공사 날짜를 냈어요. 고객이 수락하면 알림으로 알려 드려요.");
      router.refresh();
    } catch (e) {
      if (!(e instanceof ApiError)) { setSendErr(errorText(e)); return; }
      const fe = e.status === 422 ? errsOf(e) : null;
      if (fe) { dlg.current?.close(); setErrs((m) => ({ ...m, ...fe })); focusFirst(fe); return; }
      if (e.status === 409 && e.code === "invalid_state") { dlg.current?.close(); setLock(e.message); await reload(false); return; }
      if (e.status === 409 && (e.code === "request_changed" || e.code === "stale")) { dlg.current?.close(); setWarn(e.message); await reload(true); return; }
      setSendErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const title = isOpen(st) || st === "closed" ? "공사 날짜 내기" : "공사 날짜 확정";
  const formTitle = st === "requested" ? "새 날짜로 답하기" : st === "proposed" ? "날짜 다시 내기" : "날짜 고르기";
  const submit = st === "requested" ? "이 날짜로 답하기" : st === "proposed" ? "날짜 다시 내기" : "공사 날짜 내기";
  const how = f.method === "call" ? `고객에게 ‘${methodText("call", f.call_on || null)}’로 보이고, 고객이 수락해야 확정돼요.` : "고객에게 알림이 가고, 고객이 수락해야 확정돼요.";
  const offHint = weekendOff ? "주말 · 공휴일은 고를 수 없어요." : "공휴일은 고를 수 없어요. 이 집은 주말에도 공사해요.";
  const left = job.requests_left > 0 ? `고객의 다른 날짜 요청은 ${job.requests_left}번 남았어요.` : "고객은 다른 날짜를 더 요청할 수 없어요.";
  const pairDesc = ["j-dates-h", errs.dates ? "j-dates-e" : ""].filter(Boolean).join(" ");
  const fit = shown?.fit;
  const fromText = fit?.start_from && fit.end_by ? `${eunNeun(trade)} ${md(fit.start_from)}부터 ${md(fit.end_by)}까지 낼 수 있어요.`
    : fit?.start_from ? `${eunNeun(trade)} ${md(fit.start_from)}부터 낼 수 있어요.`
    : fit?.end_by ? `${eunNeun(trade)} ${md(fit.end_by)}까지 끝내야 해요.` : "";

  return (
    <>
      <div className={s.top}>
        <div className={s.headRow}>
          <div className={s.head}>
            <p className="mj-small">확정 견적서 <span className={s.code}>{q.code}</span> · 버전 {q.version} · {work}</p>
            <h1 id="j-title" className="mj-h1">{title} · {c?.name ? `${c.name} 님` : v.code}</h1>
            <p className="mj-sub mj-num">{c?.address ? `${c.address} · ${pyeongText(v.pyeong)}` : v.home_text} · {work} 확정 {md(dateSeoul(job.confirmed_at))}</p>
          </div>
          <Due job={job} />
        </div>

        <div className={s.tel}>
          <p className={s.telLab}>고객 연락처</p>
          {c?.phone ? (
            <>
              <p>{c.name} · <span className={`mj-num ${s.b} ${s.sel}`}>{phoneText(c.phone)}</span></p>
              <p className="mj-small">공사가 끝난 날부터 1년까지 보여요</p>
              <div className={s.btns}>
                <CopyPhone phone={phoneText(c.phone)} name={c.name} />
                <a className="mj-btn mj-btn-sec mj-btn-sm" href={telHref(c.phone)} aria-label={`${c.name}에게 전화 걸기`}><Phone />전화 걸기</a>
              </div>
            </>
          ) : st === "closed" ? (
            <p className="mj-small">요청이 닫혀 고객 연락처를 가렸어요.</p>
          ) : (
            <p>연락처를 보내지 않은 고객이에요 · 고객이 요청 번호 <span className={s.code}>{v.code}</span>를 말해요</p>
          )}
        </div>

        <dl className={s.brief}>
          <div><dt>확정 견적서</dt><dd><span className={s.code}>{q.code}</span> · 버전 {q.version} · <Link className={`mj-lnk ${s.inLnk}`} href={`/vendor/requests/${v.id}/quote`}>견적서 보기</Link></dd></div>
          <div><dt>낼 금액</dt><dd className="mj-num"><b>{won(q.total)}</b> <span className="mj-small">고객이 확정한 금액</span></dd></div>
          <div><dt>공사 기간</dt><dd className="mj-num">{q.days ? `${q.days}일 · 견적서에 적은 기간` : "견적서에 적지 않았어요"}</dd></div>
          <div><dt>예상 공정표</dt><dd className="mj-num">{job.plan_start && job.plan_end ? `${trade} ${span(job.plan_start, job.plan_end)}` : "없음"}</dd></div>
        </dl>
      </div>

      {note ? <DoneNote text={note} /> : null}
      {warn && !lock ? <p role="alert" className="mj-notice mj-notice-warn"><span><b>{warn}</b> 날짜를 다시 확인해 주세요.</span></p> : null}
      {lock ? <p role="alert" className="mj-notice mj-notice-bad"><span><b>{lock}</b> 이 공사에는 날짜를 낼 수 없어요.</span></p> : null}

      {st === "closed" && !lock ? (
        <p className="mj-notice mj-notice-warn"><span><b>{RESELECTED}</b> 이 공사에는 날짜를 낼 수 없어요.</span></p>
      ) : !isOpen(st) && st !== "closed" ? (
        <section aria-labelledby="j-done-t" className={s.sent}>
          <h2 id="j-done-t" className={`mj-st mj-st-ok ${s.stH}`}><Check />공사 날짜 확정</h2>
          <p className={`mj-num ${s.big}`}>{job.start_date && job.end_date ? span(job.start_date, job.end_date) : "—"}</p>
          <p className="mj-small">고객이 수락했어요.</p>
        </section>
      ) : p ? (
        <section aria-labelledby="j-sent-t" className={s.sent}>
          <div className={s.sentHead}>
            <h2 id="j-sent-t" className="mj-h3">낸 날짜</h2>
            {r ? <p className="mj-st mj-st-now">고객이 다른 날짜를 원해요</p> : <p className="mj-st mj-st-wait"><Dash />고객 수락 기다림</p>}
          </div>
          <p className={`mj-num ${s.big}`}>{span(p.start, p.end)}</p>
          <p className="mj-small mj-num">{methodText(p.method, p.call_on)} · 작업 인원 {p.crew}명 · 일하는 날 {p.workdays}일 · {stamp(p.created_at)}에 냈어요{p.changed ? " · 고객에게 ‘날짜가 바뀌었어요’로 보여요" : ""}</p>
          <p className="mj-small">{r ? "새 날짜를 낼 때까지 이 날짜가 그대로 있어요." : `고객이 수락해야 확정돼요. 수락하기 전까지 아래에서 다시 낼 수 있어요. ${left}`}</p>
        </section>
      ) : null}

      {r && open ? (
        <section aria-labelledby="j-ask-t" className={s.ask}>
          <h2 id="j-ask-t" className="mj-h3">{iGa(whoSay)} 다른 공사 날짜를 원해요 <span className="mj-small mj-num">· {stamp(r.created_at)} 요청</span></h2>
          {r.reason ? <p>&ldquo;{r.reason}&rdquo;</p> : null}
          <p className="mj-num">고객이 고른 시작일 <b>{r.wish_dates.map(md).join(" · ")}</b></p>
          <p className="mj-small mj-num">다시 낼 기한 <b className={s.ink}>{stamp(r.due_at)}</b>{r.overdue ? " · 기한이 지났어요. 자동으로 취소되지 않아요." : ""} · {left}</p>
        </section>
      ) : null}

      {open ? (
        <form aria-labelledby="j-form-t" noValidate className={s.formSec} onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <h2 id="j-form-t" className="mj-h2">{formTitle}</h2>
          <div className={s.cols}>
            <div className={s.left}>
              {r ? (
                <fieldset className="mj-fieldset">
                  <legend className="mj-label">고객이 고른 시작일에서 고르기</legend>
                  <div className={s.rads}>
                    {r.wish_dates.map((d) => (
                      <label key={d} className={`${s.rad} mj-num`}>
                        <input type="radio" name="j-wish" value={d} checked={f.start === d} onChange={() => change({ start: d })} />{md(d)}
                      </label>
                    ))}
                  </div>
                  <p className="mj-hint">고르면 시작일에 채워요. 끝나는 날은 직접 골라요.</p>
                </fieldset>
              ) : null}

              <fieldset className="mj-fieldset">
                <legend className="mj-label">정하는 방법</legend>
                <div className={s.rads}>
                  {METHODS.map((m) => (
                    <label key={m} className={s.rad}>
                      <input id={`j-method-${m}`} type="radio" name="j-method" value={m} checked={f.method === m} onChange={() => change({ method: m })} />{METHOD_LABEL[m]}
                    </label>
                  ))}
                </div>
              </fieldset>

              {f.method === "call" ? (
                <JobDateField id="j-call" label="통화한 날" past value={f.call_on} today={today} hint="오늘까지 고를 수 있어요." error={errs.call_on} onChange={(x) => change({ call_on: x })} />
              ) : null}

              <div className={s.pair}>
                <JobDateField id="j-start" label="시작일" value={f.start} today={today} holidays={holidays} weekendOff={weekendOff}
                  month={f.start || job.plan_start || undefined} describedBy={pairDesc} error={errs.start} onChange={(x) => change({ start: x })} />
                <JobDateField id="j-end" label="끝나는 날" value={f.end} today={today} holidays={holidays} weekendOff={weekendOff}
                  from={f.start || undefined} month={f.end || f.start || job.plan_end || undefined} describedBy={pairDesc} error={errs.end} onChange={(x) => change({ end: x })} />
              </div>
              <p id="j-dates-h" className={`mj-hint ${s.pairHint}`}>{offHint}</p>
              {errs.dates ? <FieldError id="j-dates-e">{errs.dates}</FieldError> : null}

              <div className="mj-field">
                <label htmlFor="j-crew" className="mj-label">작업 인원 <span className="mj-opt">(필수)</span></label>
                <div className={s.unit}>
                  <input id="j-crew" className="mj-input mj-num" inputMode="numeric" autoComplete="off" value={f.crew}
                    aria-required="true" aria-invalid={errs.crew ? true : undefined} aria-describedby={["j-crew-h", errs.crew ? "j-crew-e" : ""].filter(Boolean).join(" ")}
                    onChange={(e) => change({ crew: e.target.value.replace(/\D/g, "").slice(0, 2) })} />
                  <span aria-hidden="true">명</span>
                </div>
                <p id="j-crew-h" className="mj-hint">하루에 일하는 {crewLabel(job.trade)} 수예요. 믿집은 {job.need.crew}명으로 계산했어요.</p>
                {errs.crew ? <FieldError id="j-crew-e">{errs.crew}</FieldError> : null}
              </div>

              <dl className={`mj-num ${s.calc}`} aria-busy={calc ? true : undefined}>
                <div>
                  <dt>낸 날짜의 일하는 날</dt>
                  <dd>{shown ? <b>{shown.workdays}일</b> : <span className="mj-sub">{calc ? "계산하고 있어요" : "날짜를 고르면 나와요"}</span>}<span className={s.chip}>믿집 계산</span></dd>
                </div>
                <div>
                  <dt>필요한 기간</dt>
                  <dd>{need ? <b>{need.days}일</b> : <span className="mj-sub">{calc ? "계산하고 있어요" : "날짜를 고르면 이 인원으로 계산해요"}</span>}<span className={s.chip}>믿집 계산</span></dd>
                  {need ? <dd className={`mj-small ${s.why}`}>{needText(job.trade, need)} 품 ÷ 하루 인원을 반나절 단위로 올리고, 준비 · 마감 반나절을 더해 하루 단위로 올려요.</dd> : null}
                </div>
              </dl>
              <div role="status">
                {shown?.short ? (
                  <p className="mj-notice mj-notice-warn"><span><b>필요한 기간보다 짧아요.</b> 일하는 날 {shown.workdays}일 · 필요한 기간 {shown.need.days}일이에요. 낼 수는 있어요. 인원을 늘리거나 끝나는 날을 미뤄 보세요.</span></p>
                ) : null}
                {pvFail?.key === key && pvFail.msg ? <FieldError id="j-pv-e">{pvFail.msg}</FieldError> : null}
              </div>
            </div>

            <figure aria-labelledby="j-fit-t" className={s.right}>
              <h3 id="j-fit-t" className="mj-h3">고객 공정표와 맞춰 보기</h3>
              {job.others.length ? (
                <FitChart trade={job.trade} others={job.others} mine={f.start && f.end && f.end >= f.start ? { start: f.start, end: f.end } : null}
                  clash={clash} holidays={holidays} weekendOff={weekendOff} />
              ) : <p className="mj-small">이 집에서 날짜가 정해진 다른 공정이 없어요.</p>}
              <div role="status">
                {clash && fit ? (
                  <div className="mj-notice mj-notice-bad">
                    <span className={s.noteIco}><Alert /></span>
                    <p><b className={s.bad}>겹쳐요.</b> {fit.conflicts.map((o) => `${iGa(tradeLabel(o.trade))} ${o.vendor_name} 업체와 ${span(o.start, o.end)}에 정해졌어요.`).join(" ")} {fromText} 낼 수는 있지만 고객이 수락할 수 없어요.</p>
                  </div>
                ) : shown && job.others.length ? (
                  <div className={`mj-notice ${s.noteOk}`}>
                    <span className={`${s.noteIco} ${s.ok}`}><Check /></span>
                    <p><b className={s.ok}>겹치지 않아요.</b> 고객 공정표의 다른 공정과 날짜가 겹치지 않아요.</p>
                  </div>
                ) : null}
              </div>
            </figure>
          </div>

          <div className={s.acts}>
            <button id="j-send" type="submit" className="mj-btn mj-btn-pri" disabled={busy}>{submit}</button>
            <p className="mj-hint">{how}{st === "proposed" ? " 다시 내면 고객에게 ‘날짜가 바뀌었어요’로 보여요." : ""}</p>
          </div>
        </form>
      ) : null}

      <p className={s.links}>
        <Link className="mj-lnk" href={`/vendor/requests/${v.id}`}>요청 자세히 보기</Link>
        <Link className="mj-lnk" href="/vendor?tab=jobs">작업함 공사 탭으로 가기</Link>
      </p>

      <dialog ref={dlg} className={s.dlg} aria-labelledby="jd-t" onClose={() => { if (sent.current) { sent.current = false; return; } document.getElementById("j-send")?.focus(); }}>
        <button type="button" className={s.x} aria-label="닫기" onClick={() => dlg.current?.close()}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>
        <h2 id="jd-t" className="mj-h3">{st === "requested" ? "이 날짜로 답할까요?" : st === "proposed" ? "날짜를 다시 낼까요?" : "공사 날짜를 낼까요?"}</h2>
        <dl className={`mj-num ${s.sum}`}>
          <div><dt>공사 날짜</dt><dd><b>{f.start && f.end ? span(f.start, f.end) : "—"}</b></dd></div>
          {shown ? <div><dt>일하는 날</dt><dd>{shown.workdays}일 · 믿집 계산</dd></div> : null}
          <div><dt>작업 인원</dt><dd>{f.crew}명</dd></div>
          <div><dt>정한 방법</dt><dd>{methodText(f.method, f.call_on || null)}</dd></div>
        </dl>
        {clash ? <p className="mj-notice mj-notice-bad"><span><b>겹치는 공정이 있어요.</b> 낼 수는 있지만 고객이 수락할 수 없어요.</span></p> : null}
        {shown?.short ? <p className="mj-notice mj-notice-warn"><span><b>필요한 기간보다 짧아요.</b> 낼 수는 있어요.</span></p> : null}
        <p>{whoSay}에게 알림이 가요. 고객이 수락해야 확정되고, 그전까지 다시 낼 수 있어요.{st === "proposed" ? " 고객에게 ‘날짜가 바뀌었어요’로 보여요." : ""}</p>
        {sendErr ? <FieldError id="jd-e" alert>{sendErr}</FieldError> : null}
        <div className={s.dlgBtns}>
          <button type="button" className="mj-btn mj-btn-sec" onClick={() => dlg.current?.close()}>돌아가기</button>
          <button type="button" className="mj-btn mj-btn-pri" onClick={() => void send()} disabled={busy}>{busy ? "보내는 중이에요" : submit}</button>
        </div>
      </dialog>
    </>
  );
}
