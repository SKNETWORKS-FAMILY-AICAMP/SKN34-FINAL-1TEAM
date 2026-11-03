import { notFound, redirect } from "next/navigation";
import { JobPage } from "@/components/pro/jobs/JobPage";
import { SiteHeader } from "@/components/site/SiteHeader";
import { getVendorJob } from "@/lib/api/schedule";
import { todaySeoul } from "@/lib/format";
import { requireRole } from "@/lib/session";

export const metadata = { title: "공사 날짜 내기" };

// 공사 날짜 내기 · 다시 내기 · 고객 요청에 답하기 (시안 WVCalendar). 알림 link가 이 주소다. 내 공사가 아니면 404(있는지도 알리지 않는다).
export default async function VendorJobPage({ params }: { params: Promise<{ dealId: string }> }) {
  const { dealId } = await params;
  const me = await requireRole("vendor", `/vendor/jobs/${dealId}`);
  if (me.vendorStatus !== "approved") redirect("/vendor");
  const job = await getVendorJob(dealId);
  if (!job) notFound();
  return (
    <>
      <SiteHeader />
      <JobPage job={job} today={todaySeoul()} />
    </>
  );
}
