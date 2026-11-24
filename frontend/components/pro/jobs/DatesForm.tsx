"use client";
// 공사 날짜 올리기(V11 v_dates): 확정된 업체가 공사할 날을 하나씩 골라 보내요(시작 ~ 끝 범위가 아니에요).
// 고른 날 수 = 견적서 공사 기간이어야 보내지고, 공휴일 · 겹침은 계산 엔진이 다시 봐요(422). → POST /vendor/jobs/{did}/dates(멱등 키).
// 고객이 다른 날짜를 요청했으면(C52) 그 요청에 답하는 새 날짜예요(request_id).
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Main, PageHead } from "@/components/site/Frame";
import { BottomBar } from "@/components/ui/BottomBar";
import { Button } from "@/components/ui/Button";
import { Calendar, type DayMark, type DayNote } from "@/components/ui/Calendar";
import { SelectField } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { useToast } from "@/components/ui/Toast";
import type { JobV3 } from "@/lib/api/vendor";
import { errorText } from "@/lib/backend/client";
import { dateLong, dateSeoul, dueText, won } from "@/lib/format";
import { enc, newKey, send } from "../send";
import { Notice } from "@/components/ui/Notice";
import { Group, Lead, Row, Rows, Small } from "../ui/parts";
import { visitTitle } from "../visit/facts";
import { daysText } from "./days";
import { AssistBox } from "../assist/AssistBox";
import s from "./jobs.module.css";

/** 예시 글: 희망 시작일부터 공사 기간만큼 "12/8부터 12/10까지, 2명" */
/** 예시 글: 희망 시작일부터 공사 기간만큼(주말에 안 하는 집이면 평일로 세요) "12/8부터 12/10까지, 2명". 날짜 글만 만들어요 */
function example(start: string, n: number, noWeekend: boolean): string {
  const d = new Date(`${start}T00:00:00Z`);
  const md = (x: Date) => `${x.getUTCMonth() + 1}/${x.getUTCDate()}`;
  const off = (x: Date) => noWeekend && (x.getUTCDay() === 0 || x.getUTCDay() === 6);
  while (off(d)) d.setUTCDate(d.getUTCDate() + 1);
  const a = md(d);
  for (let left = Math.max(n, 1) - 1; left > 0; ) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (!off(d)) left -= 1;
  }
  return `${a}부터 ${md(d)}까지, 2명`;
}

const CREW = [1, 2, 3, 4, 5, 6].map((n) => ({ value: String(n), label: `${n}명` }));

export function DatesForm({ job, now }: { job: JobV3; now: string }) {
  const router = useRouter();
  const toast = useToast();
  const v = job.visit;
  const need = job.quote.days ?? job.need.days;
  const [days, setDays] = useState<string[]>(job.proposal && job.dates_state !== "requested" ? job.proposal.days : []);
  const [crew, setCrew] = useState(String(job.proposal?.crew ?? job.need.crew ?? 2));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 도우미가 말한 인원 · 날짜를 넣지 못한 까닭. 칸에는 옛 값이 남아 있어서, 업체가 인원을 다시 고르거나 달력을 누르기 전까지 보내기를 막아요
  const [crewHold, setCrewHold] = useState<string | null>(null);
  const [daysHold, setDaysHold] = useState<string | null>(null);
  const [idem] = useState(newKey);
  const today = dateSeoul(now);

  const marks: Record<string, DayMark[]> = {};
  const notes: Record<string, DayNote> = {};
  job.holidays.forEach((h) => { marks[h.day] = ["holiday"]; notes[h.day] = { text: h.name.slice(0, 2), label: h.name }; });
  if (v.work_wish_on) notes[v.work_wish_on] = { text: "희망", label: "고객 희망 시작일" };
  job.request?.wish_dates.forEach((d) => { if (!notes[d]) notes[d] = { text: "희망", label: "고객이 요청한 날" }; });

  async function go() {
    if (busy) return;
    if (daysHold || crewHold) return; // 이유는 달력 아래 · 인원 칸에 이미 보여요
    if (need && days.length !== need) { setErr(`견적서 공사 기간 ${need}일과 같게 ${need}일을 골라 주세요. 지금 ${days.length}일 골랐어요.`); return; }
    setErr(null);
    setBusy(true);
    try {
      const kind = job.dates_state === "change_requested" || job.dates_state === "scheduled" ? "change" : "initial";
      await send("POST", `/vendor/jobs/${enc(job.deal_id)}/dates`, { days, crew: Number(crew), method: "app", kind, request_id: job.request?.id ?? null }, { idem });
      toast.show("공사 날짜를 보냈어요. 고객이 수락하면 알림으로 알려 드려요.");
      router.refresh();
    } catch (e) {
      setErr(errorText(e));
      setBusy(false);
    }
  }


  return (
    <>
      <Main narrow>
        <PageHead title="공사 날짜 올리기" crumbs={[{ label: "작업함", href: "/vendor?tab=jobs" }, { label: visitTitle(v) }, { label: "공사 날짜" }]} />
        <Lead title={`${visitTitle(v)} · 확정 ${won(job.quote.total)}`} sub={[need ? `공사 ${need}일` : null, v.work_wish_on ? `고객 희망 시작 ${dateLong(v.work_wish_on)}` : null].filter(Boolean).join(" · ")} />
        {job.request ? (
          <Notice tone="info">고객이 다른 날짜를 요청했어요{job.request.reason ? ` · ${job.request.reason}` : ""} · 희망 {daysText(job.request.wish_dates)} · {dueText(job.request.due_at, { now })} 새 날짜를 보내요.</Notice>
        ) : job.dates_due_at ? <Small red>{dueText(job.dates_due_at, { now })} 올려요</Small> : null}
        <AssistBox key={job.deal_id} form="dates" resourceId={job.deal_id}
          hint="공사 날짜와 인원을 말하면 변경 후보를 보여 드려요."
          examples={[example(job.request?.wish_dates[0] ?? v.work_wish_on ?? today, need ?? 3, !job.weekend_work)]}
          current={{ days, crew: crew ? Number(crew) : null }}
          options={{
            today, baseMonth: job.request?.wish_dates[0] ?? v.work_wish_on,
            holidays: job.holidays.map((item) => item.day),
            noWeekend: !job.weekend_work,
            crewMax: 6, requiredDays: need,
          }}
          onInputIssues={(issues) => {
            if (issues.days) setDaysHold(issues.days);
            if (issues.crew) setCrewHold(issues.crew);
          }}
          onApply={(changes) => {
            if ("days" in changes) { setDays(Array.isArray(changes.days) ? changes.days : []); setDaysHold(null); }
            if ("crew" in changes) { setCrew(typeof changes.crew === "number" ? String(changes.crew) : ""); setCrewHold(null); }
            setErr(null);
          }} />
        <Group>
          <Calendar
            mode="multiple"
            label="공사할 날 고르기"
            today={today}
            month={job.request?.wish_dates[0] ?? v.work_wish_on ?? today}
            value={days}
            onChange={(x) => { setDays(x); setDaysHold(null); setErr(null); }}
            marks={marks}
            notes={notes}
            wide
          />
          <Small>공사할 날을 하나씩 눌러요. 한 번 더 누르면 빠져요.</Small>
        </Group>
        <Group>
          <Rows label="보낼 공사 날짜">
            <Row label="고른 날" sub={need ? `견적서 공사 기간 ${need}일` : undefined} value={days.length ? daysText(days, true) : "—"} />
          </Rows>
          {daysHold ? <p className={s.err} role="alert"><Icon name="error" size={16} />{daysHold}</p> : null}
          <SelectField label="인원" options={CREW} value={crew} error={crewHold} onChange={(e) => { setCrew(e.target.value); setCrewHold(null); setErr(null); }} />
          {err ? <p className={s.err} role="alert"><Icon name="error" size={16} />{err}</p> : null}
        </Group>
      </Main>
      <BottomBar note="고객이 수락하면 확정 공정표가 만들어져요">
        <Button size="lg" block onClick={go} busy={busy}>{busy ? "보내는 중" : "고객에게 보내기"}</Button>
      </BottomBar>
    </>
  );
}
