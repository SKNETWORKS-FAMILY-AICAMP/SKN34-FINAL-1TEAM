import assert from "node:assert/strict";
import { test } from "node:test";
import {
  appliedFormHolds, emptyFormHolds, formWriteBlock, manuallyResolveHolds, mergeFormHolds,
} from "./formHolds.ts";
import { manuallyEditedPeriodFields, periodIssueScope, resolvePeriodHolds } from "./periodHold.ts";
import { formKeys, validCombinedPeriod, type AssistForm, type AssistResponse, type CandidateField } from "./protocol.ts";

function response(form: AssistForm, changed: Partial<Record<string, Partial<CandidateField>>> = {}): AssistResponse {
  return {
    form, request_id: "reply", source: "llm", warnings: [],
    fields: formKeys(form).map((field) => ({
      field, action: "keep", status: "valid", value: null, evidence: "", source: "llm",
      alternatives: [], ...changed[field],
    })),
  };
}

test("모든 업무 폼은 대기 중에는 쓰지 않고 응답 후에도 미확정 칸이 남으면 쓰지 않아요", () => {
  for (const [form, edited, unresolved] of [
    ["quote", "vat", "amount"],
    ["extra", "title", "amount"],
    ["dates", "crew", "days"],
    ["period", "end", "excluded"],
  ] as const) {
    let holds = mergeFormHolds(form, emptyFormHolds(), {
      pending: true, fieldHolds: { [edited]: "후보 확인", [unresolved]: "미확정" },
    });
    assert.ok(formWriteBlock(holds), form);
    holds = mergeFormHolds(form, holds, { pending: false, fieldHolds: {} });
    assert.ok(formWriteBlock(holds), form);
    holds = appliedFormHolds(holds, { [edited]: null }, response(form));
    assert.deepEqual(holds.fields, { [unresolved]: "미확정" }, form);
    assert.ok(formWriteBlock(holds), form);
    holds = manuallyResolveHolds(holds, [edited]);
    assert.ok(formWriteBlock(holds), form);
    holds = manuallyResolveHolds(holds, [unresolved]);
    assert.equal(formWriteBlock(holds), null, form);
  }
});

test("서버 대기 자체는 필드가 없거나 직접 값을 고쳐도 쓰기를 막아요", () => {
  let holds = mergeFormHolds("quote", emptyFormHolds(), { pending: true, fieldHolds: {} });
  assert.ok(formWriteBlock(holds));
  holds = manuallyResolveHolds(holds, ["amount"]);
  assert.ok(formWriteBlock(holds));
  holds = mergeFormHolds("quote", holds, { pending: false, fieldHolds: {} });
  assert.equal(formWriteBlock(holds), null);
});

test("정확한 필드 변경분은 옛 넓은 보류에 우선하고 null만 기존 보류를 풀어요", () => {
  let holds = mergeFormHolds("period", emptyFormHolds(), {
    pending: true, period: "기간 전체", periodScope: "boundary",
    fieldHolds: { excluded: "제외일 확인", amount: "다른 폼 필드" },
  });
  assert.deepEqual(holds.fields, { excluded: "제외일 확인" });
  assert.deepEqual(holds.legacyPeriod, {});
  holds = mergeFormHolds("period", holds, { pending: false, fieldHolds: { end: "종료일 확인" } });
  assert.deepEqual(holds.fields, { excluded: "제외일 확인", end: "종료일 확인" });
  holds = mergeFormHolds("period", holds, { fieldHolds: { end: null } });
  assert.deepEqual(holds.fields, { excluded: "제외일 확인" });
  assert.ok(formWriteBlock(holds));
});

test("선택하지 않은 유효 후보는 명시적 해제 변경분이 있을 때만 풀어요", () => {
  let holds = mergeFormHolds("quote", emptyFormHolds(), {
    fieldHolds: { amount: "금액 확인", vat: "부가세 확인", days: "기간 확인" },
  });
  holds = appliedFormHolds(holds, { amount: 4_550_000 }, response("quote", {
    amount: { action: "set", value: 4_550_000 },
    vat: { action: "set", value: "included" },
    days: { action: "uncertain", status: "uncertain" },
  }));
  assert.deepEqual(holds.fields, { vat: "부가세 확인", days: "기간 확인" });
  holds = mergeFormHolds("quote", holds, { fieldHolds: { vat: null } });
  assert.deepEqual(holds.fields, { days: "기간 확인" });
  assert.ok(formWriteBlock(holds));
});

test("기존 보류 입력 형태도 계속 적용하고 섞인 제외일 미확정은 경계만 적용해도 남겨요", () => {
  let holds = mergeFormHolds("quote", emptyFormHolds(), { amount: "금액 확인" });
  assert.equal(formWriteBlock(holds), "금액 확인");
  holds = manuallyResolveHolds(holds, ["amount"]);
  assert.equal(formWriteBlock(holds), null);
  assert.deepEqual(resolvePeriodHolds({ boundary: "기간 확인" }, { end: "2026-12-20" }, response("period", {
    end: { action: "set", value: "2026-12-20" },
    excluded: { action: "uncertain", status: "uncertain" },
  })), { excluded: "기간 확인" });
});

test("제외일만 직접 바꾼 달력은 경계 보류를 풀지 않아요", () => {
  const period = { start: "2026-12-08", end: "2026-12-16", excluded: ["2026-12-10"] };
  const edited = manuallyEditedPeriodFields(period, { ...period, excluded: ["2026-12-12"] });
  assert.deepEqual(edited, ["excluded"]);
  let holds = mergeFormHolds("period", emptyFormHolds(), {
    fieldHolds: { start: "시작일 확인", end: "종료일 확인", excluded: "제외일 확인" },
  });
  holds = manuallyResolveHolds(holds, edited);
  assert.deepEqual(holds.fields, { start: "시작일 확인", end: "종료일 확인" });
  // 같은 시작을 다시 선택해도 시작일만 확인한 것으로 취급해요.
  assert.deepEqual(manuallyEditedPeriodFields(period, { ...period, end: null, excluded: [] }), ["start", "excluded"]);
  assert.deepEqual(manuallyEditedPeriodFields(
    { ...period, end: null, excluded: [] }, { ...period, excluded: [] },
  ), ["end"]);
});

test("자연스러운 하루 휴무 문장은 제외일 범위이고 기간 전체는 경계 범위예요", () => {
  for (const phrase of ["9일은 못 가요", "12월 9일은 안 돼요", "9일은 작업 불가예요"]) {
    assert.equal(periodIssueScope(phrase), "excluded", phrase);
  }
  assert.equal(periodIssueScope("9일부터 12일까지 못 가요"), "boundary");
  assert.equal(periodIssueScope("종료일은 20일로 바꾸고, 9일은 못 가요"), "boundary");
});

test("기간 부분 적용 시 62일 한도를 현재 값과 합쳐서 확인해요", () => {
  const current = { start: "2026-12-01", end: "2026-12-04", excluded: [] };
  const opts = { today: "2026-11-24" };
  assert.equal(validCombinedPeriod(current, { end: "2027-03-04" }, opts), false);
  assert.equal(validCombinedPeriod(current, { start: "2027-03-01", end: "2027-03-04" }, opts), true);
  assert.equal(validCombinedPeriod(current, { end: "2027-02-01" }, opts), true);
  assert.equal(validCombinedPeriod(current, { end: "2027-02-02" }, opts), false);
});
