// 업체가 낸 공사 날짜 칸 (시안 WSchedule 위쪽 dl): 정한 방법 줄 + 낸 날짜 · 일하는 날 · 작업 인원 · 올린 때.
// 일하는 날 · 공휴일은 API 값 그대로다(공휴일은 API 목록에서 이 날짜 안의 것만 고른다).
import { day, when } from "@/components/confirm/text";
import { WayIcon } from "@/components/vendors/icons";
import { iGa } from "@/components/vendors/text";
import { holidaysIn, methodText, offText, span, type Holiday, type Proposal } from "./text";
import s from "./schedule.module.css";

type Props = { x: Proposal; vendorName: string; weekendWork: boolean; holidays: Holiday[] };

export function DatesFacts({ x, vendorName, weekendWork, holidays }: Props) {
  const hs = holidaysIn(holidays, x.start, x.end);
  return (
    <div className={s.facts}>
      <p className={`mj-st mj-st-now mj-num ${s.stLine}`}>{x.method === "call" ? <WayIcon method="phone" /> : null}{methodText(x)}</p>
      <dl className={`mj-num ${s.dl}`}>
        <div>
          <dt className="mj-small">낸 날짜</dt>
          <dd className={s.big}>
            {span(x.start, x.end)}
            {hs.length ? <span className={s.ddSub}>{hs.map((h) => `${day(h.day)} ${h.name}`).join(", ")} 쉬어요</span> : null}
          </dd>
        </div>
        <div>
          <dt className="mj-small">일하는 날</dt>
          <dd className={s.big}>{x.workdays}일 <span className={s.ddTail}>믿집 계산 · {offText(weekendWork)}</span></dd>
        </div>
        <div>
          <dt className="mj-small">작업 인원</dt>
          <dd className={s.big}>{x.crew}명 <span className={s.ddTail}>{iGa(vendorName)} 적은 값</span></dd>
        </div>
        <div>
          <dt className="mj-small">올린 때</dt>
          <dd className={s.big}>{when(x.created_at)} <span className={s.ddTail}>{iGa(vendorName)} 올림</span></dd>
        </div>
      </dl>
    </div>
  );
}
