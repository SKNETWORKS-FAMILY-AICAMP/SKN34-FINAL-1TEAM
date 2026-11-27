// 기존 화면 파서 자체를 호출해 규칙 후보를 평가한다. 별도 정규식은 만들지 않는다.
import { createInterface } from "node:readline";
import { performance } from "node:perf_hooks";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const source = process.argv[2]
  ? pathToFileURL(resolve(process.argv[2])).href
  : new URL("../../frontend/components/pro/assist/parse.ts", import.meta.url).href;

let parser;
try {
  parser = await import(source);
} catch (error) {
  process.stderr.write(`기존 TS 파서를 불러오지 못했습니다: ${String(error)}\n`);
  process.exit(2);
}

const fieldsByForm = {
  period: ["start", "end", "excluded"],
  quote: ["amount", "vat", "days", "valid"],
  dates: ["days", "crew"],
  extra: ["title", "reason", "amount", "vat"],
};

function parseExisting(row) {
  switch (row.form) {
    case "period":
      return parser.parsePeriod(row.text, row.today, row.base_month);
    case "quote":
      return parser.parseQuote(row.text, row.today);
    case "dates":
      if (typeof parser.parseDates !== "function") throw Error("기존 TS 파서에 parseDates가 없습니다. #176 통합 후 다시 실행하세요.");
      return parser.parseDates(row.text, row.today, row.base_month, row.context?.holidays ?? [], row.context?.no_weekend ?? false);
    case "extra":
      if (typeof parser.parseExtra !== "function") throw Error("기존 TS 파서에 parseExtra가 없습니다. #176 통합 후 다시 실행하세요.");
      return parser.parseExtra(row.text, row.today);
    default:
      throw Error(`모르는 화면: ${row.form}`);
  }
}

function candidateFields(row, parsed) {
  const names = fieldsByForm[row.form];
  if (!names) throw Error("평가 화면이 아닙니다");
  return names.map((field) => {
    const value = parsed?.[field];
    // 미추출은 삭제 지시가 아니므로 keep. 배열의 빈 값도 자동 삭제하지 않는다.
    const set = value !== undefined && value !== null && (!Array.isArray(value) || value.length > 0);
    return { field, action: set ? "set" : "keep", value: set ? value : null, evidence: set ? row.text : "" };
  });
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (!line.trim()) continue;
  let id = null;
  const started = performance.now();
  try {
    const row = JSON.parse(line);
    id = row.id;
    const fields = candidateFields(row, parseExisting(row));
    process.stdout.write(`${JSON.stringify({ id, fields, latency_ms: performance.now() - started, error: null })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ id, fields: null, latency_ms: performance.now() - started, error: String(error) })}\n`);
  }
}
