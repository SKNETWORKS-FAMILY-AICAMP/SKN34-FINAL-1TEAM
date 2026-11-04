// 내 공사 목록 표 (시안 WMy). 단계 · 다음 할 일 · 바뀐 날은 서버 값을 그대로 보여 준다.
// 폰 너비에서는 줄마다 카드처럼 쌓고, 칸 이름을 앞에 붙인다.
import Link from "next/link";
import type { ApiProjectItem } from "@/lib/api/project";
import { dateSeoul, md } from "@/lib/format";
import { gradeLabel, projectTitle, sizeLabel, tradeLabel } from "@/lib/labels";
import { PHASE_TEXT, PHASE_TONE, stepText, type Phase } from "./text";
import s from "./my.module.css";

const TRADES = ["dobae", "mokgong"];

function PhaseMark({ phase }: { phase: Phase }) {
  if (phase === "done") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M3 8.5l3.2 3.2L13 4.5" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6.3" stroke="currentColor" strokeWidth="1.7" />
      {phase === "estimate" ? null : <circle cx="8" cy="8" r="3" fill="currentColor" />}
    </svg>
  );
}

/** 폰 너비에서만 보이는 칸 이름 (표 머리는 화면 읽기 프로그램이 읽는다) */
function Lab({ children }: { children: string }) {
  return <span className={s.lab} aria-hidden="true">{children}</span>;
}

function Row({ p }: { p: ApiProjectItem }) {
  const title = projectTitle(p.region, p.trades);
  const trades = TRADES.filter((t) => p.trades.includes(t));
  return (
    <tr>
      <th scope="row">
        <Link className={s.name} href={`/projects/${p.id}`}>{title}</Link>
        <span className={`mj-code ${s.blk}`}>{p.code}</span>
      </th>
      <td>
        <Lab>집</Lab>
        <div>{p.region}<span className={`mj-small ${s.blk}`}>아파트 {sizeLabel(p.size)}</span></div>
      </td>
      <td>
        <Lab>공정</Lab>
        <div>
          {trades.map((t) => {
            const grade = t === "dobae" ? p.grades[t] : null;
            return <span key={t} className={s.blk}>{grade ? `${tradeLabel(t)} ${gradeLabel(grade)}` : tradeLabel(t)}</span>;
          })}
        </div>
      </td>
      <td>
        <Lab>지금 단계</Lab>
        <div>
          <span className={`mj-st ${PHASE_TONE[p.phase]}`}><PhaseMark phase={p.phase} />{PHASE_TEXT[p.phase]}</span>
          <span className={`mj-small ${s.blk}`}>{stepText(p.current)}</span>
        </div>
      </td>
      <td className={s.todo}>
        <Lab>다음 할 일</Lab>
        <div>
          {p.todo ? (
            <>
              <Link className="mj-lnk" href={p.todo.link}>{p.todo.action}<span className="mj-sr"> · {title}</span></Link>
              <span className={`mj-small ${s.blk}`}>{p.todo.text}</span>
            </>
          ) : <span className="mj-small">지금 할 일이 없어요</span>}
        </div>
      </td>
      <td className={s.when}>
        <Lab>바뀐 날</Lab>
        <div>{md(dateSeoul(p.updated_at))}</div>
      </td>
    </tr>
  );
}

export function ProjectTable({ items }: { items: ApiProjectItem[] }) {
  return (
    <div className="mj-table-box" tabIndex={0} role="region" aria-label="공사 목록 표">
      <table className={`mj-table mj-num ${s.tbl}`}>
        <caption className="mj-sr">내 공사 {items.length}건: 공사 · 집 · 공정 · 지금 단계 · 다음 할 일 · 바뀐 날</caption>
        <thead>
          <tr>
            <th scope="col">공사</th>
            <th scope="col">집</th>
            <th scope="col">공정</th>
            <th scope="col">지금 단계</th>
            <th scope="col">다음 할 일</th>
            <th scope="col" className={s.when}>바뀐 날</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => <Row key={p.id} p={p} />)}
        </tbody>
      </table>
    </div>
  );
}
