// 실제 폼과 저장 호출을 검증하되 서버 응답은 시험 전용 HTTP 서버로 고정한다.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e/assist",
  testMatch: /.*\.spec\.ts$/,
  outputDir: "./e2e/.out/assist-results",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3188",
    channel: process.env.CI ? undefined : "chrome",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 360, height: 800 } } },
  ],
  webServer: [
    { command: "node --experimental-strip-types --no-warnings e2e/assist/api.mjs", url: "http://127.0.0.1:8188/health/live", reuseExistingServer: false },
    {
      command: "pnpm exec next dev --port 3188",
      url: "http://localhost:3188/offline",
      env: { API_INTERNAL_URL: "http://127.0.0.1:8188", NEXT_PUBLIC_MJ_MOCK: "0", NEXT_DIST_DIR: ".next-assist", NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
