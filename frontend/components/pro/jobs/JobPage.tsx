// 공사 날짜 내기 화면 틀 (시안 WVCalendar): 이동 경로 + 공사 날짜 칸(JobBoard).
import Link from "next/link";
import { workText } from "@/components/pro/inbox/text";
import { JobBoard } from "./JobBoard";
import type { Job } from "./text";
import s from "./jobs.module.css";

export function JobPage({ job, today }: { job: Job; today: string }) {
  const v = job.visit;
  const here = `${v.customer?.name ?? v.code} · ${v.area_name} ${workText(v)} 공사 날짜`;
  return (
    <>
      <nav className={`mj-wrap mj-crumb ${s.crumb}`} aria-label="이동 경로">
        <ol>
          <li><Link href="/vendor">작업함</Link><span aria-hidden="true">›</span></li>
          <li><Link href="/vendor?tab=jobs">공사</Link><span aria-hidden="true">›</span></li>
          <li><span aria-current="page">{here}</span></li>
        </ol>
      </nav>
      <main id="main" className={`mj-wrap ${s.page}`}>
        <JobBoard initial={job} today={today} />
      </main>
    </>
  );
}
