// 이전 후보의 타입·출처·확정 술어를 실제 세션의 적용 경로에서 확인해요.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { AssistCurrent, AssistForm, AssistRequest, AssistValue, CandidateField, RuleOptions } from "./protocol.ts";
import { formKeys } from "./protocol.ts";
import { AssistSession } from "./session.ts";
import { requestIntent } from "./requestIntent.ts";

type Example = {
  form: AssistForm;
  first: string;
  second: string;
  current: AssistCurrent;
  firstValue: AssistValue;
  secondOld: AssistValue;
  secondNew: AssistValue;
  firstText: string;
  secondText: string;
  retain: string;
  currentRetain: string;
  wrongNoun: string;
  competing: string;
  changedFirst: AssistValue;
  changedText: string;
};

const examples: Example[] = [
  {
    form: "quote", first: "vat", second: "valid",
    current: { amount: 3200000, days: 4, vat: "included", valid: "2026-12-17" },
    firstValue: "excluded", secondOld: "2026-12-21", secondNew: "2026-12-27",
    firstText: "견적 유효기간은 12월 21일, 세금은 별도 조건으로 후보 등록해 주세요.",
    secondText: "견적 유효기한을 12월 27일로 늘려 주실래요?",
    retain: "세금 조건은 처음 정했던 방식, 만료 날짜는 방금 연장한 날짜로 적용할게요.",
    currentRetain: "세금 조건은 현재 입력값, 만료 날짜는 방금 연장한 날짜로 적용해 주세요.",
    wrongNoun: "세금 조건은 처음 정했던 금액, 만료 날짜는 방금 연장한 날짜로 적용해 주세요.",
    competing: "부가세는 면세로 변경해 주세요.",
    changedFirst: "exempt", changedText: "부가세는 면세로 지정해 주세요.",
  },
  {
    form: "period", first: "start", second: "end",
    current: { start: "2026-12-02", end: "2026-12-18", excluded: [] },
    firstValue: "2026-12-05", secondOld: "2026-12-22", secondNew: "2026-12-29",
    firstText: "실측 시작일은 12월 5일, 종료일은 12월 22일로 정해 주세요.",
    secondText: "종료일은 12월 29일로 연장해 주세요.",
    retain: "시작일은 최초 정했던 날, 종료일은 방금 연장한 날짜로 반영해 주세요.",
    currentRetain: "시작일은 현재 입력값, 종료일은 방금 연장한 날짜로 반영해 주세요.",
    wrongNoun: "시작일은 처음 정했던 금액, 종료일은 방금 연장한 날짜로 반영해 주세요.",
    competing: "시작일은 12월 7일로 변경해 주세요.",
    changedFirst: "2026-12-07", changedText: "실측 시작일은 12월 7일로 정해 주세요.",
  },
  {
    form: "dates", first: "days", second: "crew",
    current: { days: ["2026-12-08", "2026-12-12"], crew: 2 },
    firstValue: ["2026-12-10", "2026-12-16"], secondOld: 3, secondNew: 4,
    firstText: "작업일은 12월 10일과 16일로 잡고 인원은 3명으로 정해 주세요.",
    secondText: "작업 인원을 4명으로 늘려 주세요.",
    retain: "작업 일정은 처음 잡았던 목록, 작업 인원은 방금 늘린 인원으로 사용하겠습니다.",
    currentRetain: "작업 일정은 현재 입력값, 작업 인원은 방금 늘린 인원으로 사용하겠습니다.",
    wrongNoun: "작업 일정은 처음 정했던 금액, 작업 인원은 방금 늘린 인원으로 사용하겠습니다.",
    competing: "작업일은 12월 11일과 17일로 변경해 주세요.",
    changedFirst: ["2026-12-11", "2026-12-17"], changedText: "작업일은 12월 11일과 17일로 잡아 주세요.",
  },
  {
    form: "extra", first: "title", second: "reason",
    current: { title: "기존 보강", reason: "기존 손상", amount: 200000, vat: "included" },
    firstValue: "천장 보강", secondOld: "마감 들뜸", secondNew: "천장 균열",
    firstText: "추가 항목명은 천장 보강, 사유는 마감 들뜸으로 적어 주세요.",
    secondText: "사유는 천장 균열로 변경해 주세요.",
    retain: "항목명은 처음 정했던 제목, 사유는 방금 변경한 이유로 반영할게요.",
    currentRetain: "항목명은 현재 입력값, 사유는 방금 변경한 이유로 반영할게요.",
    wrongNoun: "항목명은 처음 정했던 금액, 사유는 방금 변경한 이유로 반영할게요.",
    competing: "항목명은 벽체 보강으로 변경해 주세요.",
    changedFirst: "벽체 보강", changedText: "항목명은 벽체 보강으로 정해 주세요.",
  },
];

const options: RuleOptions = {
  today: "2026-11-25", baseMonth: "2026-12-01", requiredDays: 2,
  daysMax: 15, crewMax: 6, vatModes: ["included", "excluded", "exempt"],
};
const resource = "00000000-0000-4000-8000-000000000179";

function keep(request: AssistRequest, field: string): CandidateField {
  return { field, action: "keep", status: "valid", value: null, evidence: "", source: "rule",
    alternatives: [], intent_kind: "no_mention", origin_request_id: request.request_id };
}
function set(request: AssistRequest, field: string, value: AssistValue, evidence = request.text): CandidateField {
  return { field, action: "set", status: "valid", value, evidence, source: "llm",
    alternatives: [], intent_kind: "explicit_change", value_source: "utterance", origin_request_id: request.request_id };
}
function carry(request: AssistRequest, field: string): CandidateField {
  const prior = request.previous_candidate;
  const old = prior?.fields.find(f => f.field === field);
  assert.ok(prior && old);
  return { ...old, status: "valid", alternatives: [], source: prior.sources![field],
    origin_request_id: prior.origins![field], intent_kind: "retain_previous", value_source: "previous" };
}

function harness(e: Example) {
  let current = structuredClone(e.current);
  const versions: Record<string, number> = {};
  const holds: Record<string, string> = {};
  let pending = false;
  let request: AssistRequest;
  let reply: (request: AssistRequest) => CandidateField[];
  const session = new AssistSession(e.form, resource, async r => {
    request = structuredClone(r);
    const fields = reply(r);
    return { request_id: r.request_id, form: r.form, source: "llm", warnings: [],
      fields: formKeys(r.form).map(name => fields.find(f => f.field === name) ?? keep(r, name)) };
  });
  const getLive = () => ({ current: structuredClone(current), manualVersions: { ...versions } });
  async function send(text: string, respond: (request: AssistRequest) => CandidateField[]) {
    reply = respond;
    let done!: () => void;
    const ready = new Promise<void>(resolve => { done = resolve; });
    const off = session.subscribe(view => { if (view.phase === "ready" && view.utterance === text) done(); });
    assert.equal(session.send(text, current, options, delta => {
      if (typeof delta.pending === "boolean") pending = delta.pending;
      for (const [key, value] of Object.entries(delta.fieldHolds ?? {})) {
        if (typeof value === "string") holds[key] = value;
        else if (value === null) delete holds[key];
      }
    }, getLive), true);
    assert.equal(pending, true);
    await ready;
    off();
    assert.equal(pending, false);
    return request!;
  }
  function manual(field: string, value: AssistValue) {
    current[field] = value;
    versions[field] = (versions[field] ?? 0) + 1;
    delete holds[field];
  }
  function apply(select: string[]) {
    for (const f of session.view.response!.fields) session.select(f.field, select.includes(f.field));
    let changes: AssistCurrent = {};
    const applied = session.apply(current, patch => { changes = structuredClone(patch); Object.assign(current, patch); });
    return { applied, changes };
  }
  function field(name: string) { return session.view.response!.fields.find(f => f.field === name)!; }
  async function seed(changeFirst = false) {
    const r1 = await send(e.firstText, r => [set(r,e.first,e.firstValue),set(r,e.second,e.secondOld)]);
    const r2 = await send(changeFirst ? e.changedText : e.secondText, r => changeFirst
      ? [set(r,e.first,e.changedFirst),carry(r,e.second)]
      : [carry(r,e.first),set(r,e.second,e.secondNew)]);
    return { r1, r2 };
  }
  return { session, send, manual, apply, field, seed, holds, getLive };
}

for (const e of examples) {
  test(e.form + " shared final predicate keeps each candidate's actual origin", async () => {
    const h = harness(e);
    try {
      const { r1,r2 } = await h.seed();
      const r3 = await h.send(e.retain, r => [carry(r,e.first),carry(r,e.second)]);
      assert.equal(r3.previous_candidate?.origins?.[e.first],r1.request_id);
      assert.equal(r3.previous_candidate?.origins?.[e.second],r2.request_id);
      assert.equal(h.field(e.first).origin_request_id,r1.request_id);
      assert.equal(h.field(e.second).origin_request_id,r2.request_id);
      assert.deepEqual(h.getLive().current,e.current);
      assert.deepEqual(h.apply([e.first,e.second]),{ applied:true,changes:{[e.first]:e.firstValue,[e.second]:e.secondNew} });
      assert.deepEqual(h.holds,{});
    } finally { h.session.dispose(); }
  });

  for (const variant of ["fresh_origin", "missing", "stale", "first_replaced", "wrong_noun", "competing", "current"] as const) {
    test(e.form + " protects scoped retention: " + variant, async () => {
      const h = harness(e);
      try {
        const { r1,r2 } = await h.seed(variant === "first_replaced");
        if (variant === "missing") h.session.cancel();
        if (variant === "stale") h.manual(e.first,e.changedFirst);
        const text = variant === "wrong_noun" ? e.wrongNoun : variant === "current" ? e.currentRetain :
          variant === "competing" ? e.retain + " " + e.competing : e.retain;
        const r3 = await h.send(text,r => {
          const first = r.previous_candidate?.fields.some(f => f.field === e.first)
            ? carry(r,e.first) : { ...set(r,e.first,e.firstValue,e.firstText), origin_request_id:r1.request_id,
              intent_kind:"retain_previous" as const,value_source:"previous" as const };
          if (variant === "fresh_origin") Object.assign(first,{origin_request_id:r.request_id,intent_kind:"explicit_change",value_source:"utterance",evidence:r.text});
          const second = r.previous_candidate?.fields.some(f => f.field === e.second)
            ? carry(r,e.second) : { ...set(r,e.second,e.secondNew,e.secondText), origin_request_id:r2.request_id };
          return [first,second];
        });
        assert.equal(h.field(e.first).status,"uncertain");
        assert.equal(h.field(e.first).action,"uncertain");
        assert.ok(h.holds[e.first]);
        if (variant === "missing") {
          assert.equal(r3.previous_candidate,null);
          assert.equal(h.field(e.second).status,"uncertain");
          assert.deepEqual(h.apply([e.first,e.second]),{applied:false,changes:{}});
        } else {
          if (variant === "stale") assert.equal(r3.previous_candidate?.fields.some(f=>f.field===e.first),false);
          const value = variant === "first_replaced" ? e.secondOld : e.secondNew;
          assert.equal(h.field(e.second).status,"valid");
          assert.deepEqual(h.apply([e.first,e.second]),{applied:true,changes:{[e.second]:value}});
          assert.ok(h.holds[e.first]);
        }
      } finally { h.session.dispose(); }
    });
  }

  for (const [label, transform] of [
    ["question", (s:string)=>s.replace(/(?:적용|반영|사용)(?:할게요|해 주세요|하겠습니다)\.$/,"적용할까요?")],
    ["negative", (s:string)=>s.replace(/(?:적용|반영|사용)(?:할게요|해 주세요|하겠습니다)\.$/,"적용하지 마세요.")],
    ["conditional", (s:string)=>s.replace(/(?:적용|반영|사용)(?:할게요|해 주세요|하겠습니다)\.$/,"조건이 맞으면 적용해 주세요.")],
    ["incomplete", (s:string)=>s.replace(/\s*(?:적용|반영|사용)(?:할게요|해 주세요|하겠습니다)\.$/,".")],
  ] as const) {
    test(e.form + " rejects shared " + label,async () => {
      const h = harness(e);
      try {
        await h.seed();
        const text = transform(e.retain);
        assert.notEqual(text,e.retain);
        await h.send(text,r=>[carry(r,e.first),carry(r,e.second)]);
        assert.equal(h.field(e.first).status,"uncertain");
        assert.notEqual(h.field(e.second).status,"valid");
        assert.deepEqual(h.apply([e.first,e.second]),{applied:false,changes:{}});
        assert.deepEqual(h.getLive().current,e.current);
      } finally { h.session.dispose(); }
    });
  }

  test(e.form + " manual after response prevents overwriting selected retained field",async () => {
    const h = harness(e);
    try {
      await h.seed();
      await h.send(e.retain,r=>[carry(r,e.first),carry(r,e.second)]);
      h.manual(e.first,e.changedFirst);
      assert.deepEqual(h.apply([e.first,e.second]),{applied:false,changes:{}});
      assert.deepEqual(h.apply([e.second]),{applied:true,changes:{[e.second]:e.secondNew}});
      assert.deepEqual(h.getLive().current[e.first],e.changedFirst);
    } finally { h.session.dispose(); }
  });

  test(e.form + " confirmed current reference and independent prior retention",async () => {
    const h = harness(e);
    try {
      await h.seed();
      await h.send(e.currentRetain,r=>[
        {...keep(r,e.first),intent_kind:"retain_current",value_source:"current",evidence:e.currentRetain.split(",")[0]},
        carry(r,e.second),
      ]);
      assert.equal(h.field(e.first).action,"keep");
      assert.equal(h.holds[e.first],undefined);
      assert.deepEqual(h.apply([e.second]),{applied:true,changes:{[e.second]:e.secondNew}});
    } finally { h.session.dispose(); }
  });
}

test("typed quote prior aliases identify the separate fields without assigning values",()=>{
  const i = requestIntent("quote",examples[0].retain);
  assert.deepEqual([...i.priorReferences.entries()],[["vat","first"],["valid","previous"]]);
  assert.deepEqual([...i.blocked],[]);
  assert.deepEqual([...i.requested].sort(),["valid","vat"]);
});

// 실행된 3턴의 원래 날짜 후보 출처와 수동 인원 변경을 그대로 재현해요.
// 진행형 표현을 허용해도 문의·부정·조건·새 출처는 적용하지 않아요.
const firstHistoryDays = ["2026-12-10","2026-12-16"];
const firstHistoryEvidence = "12월 10일하고 16일, 이틀만 작업";
const manualCrewEvidence = "인원은 제가 직접 화면에서 네 명으로 수정해 뒀으니 그 값으로 유지";
for (const [ending,allowed,forged] of [
  ["가죠.",true,false],
  ["가겠습니다.",true,false],
  ["가 주세요.",true,false],
  ["가주세요.",true,false],
  ["가죠?",false,false],
  ["가지 마세요.",false,false],
  ["조건이 맞으면 가겠습니다.",false,false],
  ["가겠습니다.",false,true],
] as const) {
  test("first workday history / manual crew / " + ending + (forged ? " wrong origin" : ""),async()=>{
    const e = {...examples[2],current:{days:["2026-12-07","2026-12-14"],crew:3}};
    const h = harness(e);
    try {
      const r1 = await h.send("12월 10일하고 16일, 이틀만 작업 들어가는 걸로 일정 수정해 주세요.",
        r=>[set(r,"days",firstHistoryDays,firstHistoryEvidence)]);
      const r2 = await h.send("인력은 여섯 명으로 늘려 주시고요, 날짜는 앞에서 말씀드린 일정대로 해 주세요.",
        r=>[carry(r,"days"),set(r,"crew",6,"인력은 여섯 명으로 늘려 주시고요")]);
      h.manual("crew",4);
      const text = manualCrewEvidence + "하고요. 작업일은 처음에 요청했던 일정 그대로 " + ending;
      const r3 = await h.send(text,r=>[
        {...carry(r,"days"),...(forged ? {origin_request_id:r2.request_id} : {})},
        {...keep(r,"crew"),intent_kind:"retain_current",value_source:"current",evidence:manualCrewEvidence},
      ]);
      assert.equal(r3.previous_candidate?.origins?.days,r1.request_id);
      assert.equal(r3.previous_candidate?.fields.some(f=>f.field==="crew"),false);
      assert.equal(h.field("days").status==="valid" && h.field("days").action==="set",allowed);
      assert.deepEqual(h.getLive().current,{days:e.current.days,crew:4});
      assert.deepEqual(h.apply(["days","crew"]),allowed
        ? {applied:true,changes:{days:firstHistoryDays}} : {applied:false,changes:{}});
      assert.equal(h.getLive().current.crew,4);
      assert.deepEqual(h.getLive().current.days,allowed ? firstHistoryDays : e.current.days);
      if (allowed) assert.equal(h.holds.days,undefined);
      else assert.ok(h.holds.days);
    } finally { h.session.dispose(); }
  });
}
