// 업체 도우미 화면 시험 전용 서버. 실제 업무 API와 데이터에는 연결하지 않는다.
import { createServer } from "node:http";
import { MOCK_VISITS, MOCK_QUOTE, MOCK_JOBS, MOCK_PROFILE } from "../../lib/mock/v3c/vendor.ts";

const port = Number(process.env.ASSIST_TEST_API_PORT || 8188);
let requests = [];
let pending = [];
const clone = (value) => structuredClone(value);
const json = (res, value, status = 200) => {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(value));
};
const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  let body = "";
  for await (const chunk of req) body += chunk;
  const data = body ? JSON.parse(body) : null;
  if (url.pathname === "/health/live") return json(res, { ok: true });
  if (url.pathname === "/__assist-test") {
    if (req.method === "DELETE") { requests = []; pending = []; }
    if (req.method === "POST") pending.push(data);
    return json(res, { requests, pending: pending.length });
  }
  requests.push({ method: req.method, path: url.pathname, body: data });
  if (url.pathname === "/vendor/assist/interpret") {
    const next = pending.shift();
    if (next?.delay) await new Promise((resolve) => setTimeout(resolve, next.delay));
    if (next?.status && next.status !== 200) {
      return json(res, { error: { code: "test_failure", message: next.message || "잠시 뒤 다시 보내 주세요." } }, next.status);
    }
    return json(res, {
      request_id: data.request_id, form: data.form, source: "llm", warnings: [],
      fields: next?.fields || data.rule_fields.map((field) => ({ ...field, source: "llm", status: "valid", alternatives: [] })),
    });
  }
  if (url.pathname === "/me") return json(res, {
    id: "00000000-0000-4000-8000-00000000a001", email: "assist@example.test", name: "도우미 시험 업체",
    role: "vendor", vendor_status: "approved", vendor_reject_reason: null, phone: null, created_at: "2026-11-01T00:00:00Z",
  });
  if (url.pathname === "/notifications/unread") return json(res, { unread: 0 });
  if (url.pathname === "/notifications") return json(res, { items: [], unread: 0 });
  if (url.pathname === "/vendor/profile") return json(res, MOCK_PROFILE);
  if (url.pathname.endsWith("/extras/preview")) return json(res, { supply: 120000, vat: 0, total: 120000 });
  const quote = url.pathname.match(/^\/vendor\/visits\/([^/]+)\/quote$/);
  if (quote) {
    const board = clone(MOCK_QUOTE);
    board.visit_id = quote[1];
    if (req.method === "PUT") Object.assign(board.draft, data);
    return json(res, board);
  }
  const visit = url.pathname.match(/^\/vendor\/visits\/([^/]+)$/);
  if (visit && MOCK_VISITS[visit[1]]) {
    const item = clone(MOCK_VISITS[visit[1]]);
    item.new = false;
    item.photos = [];
    item.photo_labels = [];
    item.open_count = 4;
    return json(res, item);
  }
  const job = url.pathname.match(/^\/vendor\/jobs\/([^/]+)$/);
  if (job && MOCK_JOBS[job[1]]) return json(res, MOCK_JOBS[job[1]]);
  if (url.pathname.endsWith("/log")) return json(res, []);
  return json(res, { error: { code: "not_found", message: "시험 경로가 없어요." } }, 404);
});
server.listen(port, "127.0.0.1", () => console.log(`assist test API ${port}`));
