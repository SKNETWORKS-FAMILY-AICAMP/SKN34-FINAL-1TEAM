import { SiteHeader } from "@/components/site/SiteHeader";
import { ApplicationHome } from "@/components/pro/home/ApplicationHome";
import { FirstDay } from "@/components/pro/home/FirstDay";
import { getMyApplication, getMyPrices, getMyProfile } from "@/lib/api/vendor";
import { requireRole } from "@/lib/session";

export const metadata = { title: "업체 홈" };

const TRADES = ["dobae", "mokgong"] as const;

// 업체 홈. 승인 전이면 가입 심사(시안 WVHome), 승인됐으면 작업함(지금은 받은 요청이 없는 첫날 모습, WVFirst).
export default async function VendorHome() {
  await requireRole("vendor", "/vendor");
  const app = await getMyApplication();
  if (!app) {
    return (
      <>
        <SiteHeader />
        <main id="main" className="mj-wrap mj-main">
          <h1 className="mj-h1">업체 홈</h1>
          <p className="mj-sub">업체 신청을 찾을 수 없어요. 다시 로그인해 주세요.</p>
        </main>
      </>
    );
  }
  if (app.status !== "approved") {
    return (
      <>
        <SiteHeader />
        <ApplicationHome app={app} />
      </>
    );
  }
  // 처음 할 일을 마쳤는지는 지금 있는 읽기로만 본다:
  // 견적서 = 공정 중 하나라도 확인한 단가 묶음이 있음, 실측 방식 = 연락 방식을 정함,
  // 활동 지역 = 업체 정보를 한 번이라도 저장함(저장할 때 활동 지역도 함께 낸다).
  const mine = TRADES.filter((t) => app.trades.includes(t));
  const [prices, profile] = await Promise.all([
    Promise.all(mine.map((t) => getMyPrices(t).catch(() => null))),
    getMyProfile().catch(() => null),
  ]);
  const done = {
    prices: prices.some((p) => !!p?.confirmed),
    visit: !!profile?.contact_method,
    area: !!profile?.updated_at,
  };
  return (
    <>
      <SiteHeader />
      <FirstDay app={app} done={done} />
    </>
  );
}
