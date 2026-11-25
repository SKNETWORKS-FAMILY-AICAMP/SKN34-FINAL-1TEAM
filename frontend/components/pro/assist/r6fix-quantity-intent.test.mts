// R6 일수와 날짜 별칭의 실제 세션 안전 회귀. 서버 응답은 모두 스텁이에요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistCurrent, type AssistRequest, type CandidateField } from "./protocol.ts";
import { emptyFormHolds, mergeFormHolds, manuallyResolveHolds } from "./formHolds.ts";
import { parseQuote, readCrew, parseDates } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { requestIntent } from "./requestIntent.ts";

const opts = { today: "2026-11-24", baseMonth: "2026-12-01", daysMax: 365, crewMax: 6, vatModes: ["included","excluded","exempt"] };
const quote = { amount: 4550000, days: 3, vat: "included", valid: "2026-12-17" };
const dates = { days: ["2026-12-09","2026-12-16"], crew: 2 };
const period = { start: "2026-12-02", end: "2026-12-24", excluded: [] };
const extra = { amount: 1000000, vat: "included", title: "보강", reason: "현장 요청" };
type Fields = Record<string,Partial<CandidateField>>;
function set(value: AssistCurrent[string], evidence: string): Partial<CandidateField> {
  return { action: "set", value, evidence, intent_kind: "explicit_change", value_source: "utterance", source: "llm" };
}
function fixture(form: AssistForm = "quote", initial: AssistCurrent = quote) {
  let current = initial, holds = emptyFormHolds();
  const manualVersions: Record<string,number> = {};
  let job: { request: AssistRequest; resolve: (v: unknown) => void; reject: (e: Error) => void } | undefined;
  const session = new AssistSession(form,"r6fix",request => new Promise((resolve,reject) => { job={request,resolve,reject}; }));
  function send(text:string) {
    assert(session.send(text,current,opts,issues=>{holds=mergeFormHolds(form,holds,issues);},()=>({current,manualVersions})));
    assert(job);
    assert(holds.pending);
    assert.equal(session.apply(current,()=>assert.fail("응답 전 적용")),false);
    return job.request;
  }
  async function resolve(fields:Fields) {
    assert(job);
    const request=job.request;
    job.resolve({request_id:request.request_id,form,source:"llm",warnings:[],
      fields:formKeys(form).map(field=>({field,status:"valid",source:"rule",action:"keep",value:null,evidence:"",
        intent_kind:"no_mention",value_source:null,origin_request_id:request.request_id,alternatives:[],...fields[field]}))});
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase,"ready");
    assert.equal(holds.pending,false);
  }
  async function offline() {
    assert(job);
    job.reject(new Error("offline"));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(session.view.phase,"ready");
  }
  function apply(expected:AssistCurrent) {
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,v=>{patch=v;}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);
  }
  function manual(field:string,value:AssistCurrent[string]) {
    current={...current,[field]:value};
    manualVersions[field]=(manualVersions[field]??0)+1;
    holds=manuallyResolveHolds(holds,[field]);
  }
  return {session,send,resolve,offline,apply,manual,get holds(){return holds;},get job(){return job!;}};
}

test("R6-01: 한글 금액과 고유어 공사 일수를 각각 검증해 적용해요",async()=>{
  const f=fixture();
  const text="견적서에 육백삼십이만 오천 원 찍고 시공은 닷새로 잡아주세요.";
  const req=f.send(text);
  assert.equal(req.rule_fields.find(x=>x.field==="days")?.value,5);
  await f.resolve({amount:set(6325000,"육백삼십이만 오천 원"),days:set(5,"시공은 닷새로 잡아주세요")});
  f.apply({amount:6325000,days:5});
  assert.deepEqual(f.holds.fields,{});
});
test("R6-01 과거 서버 invalid는 로컬에서 되살리지 않아요",async()=>{
  const f=fixture();
  f.send("견적서에 육백삼십이만 오천 원 찍고 시공은 닷새로 잡아주세요.");
  await f.resolve({amount:set(6325000,"육백삼십이만 오천 원"),days:{action:"uncertain",status:"invalid",value:null,evidence:"시공은 닷새로 잡아주세요",reason_code:"unsupported_normalization"}});
  f.apply({amount:6325000});
  assert.ok(f.holds.fields.days);
});
test("R6-02 저장 서버 오답: 효력 문의는 막고 별도 세금만 적용해요",async()=>{
  const f=fixture();
  const req=f.send("부가세 별도 처리로 확정할게요. 견적 효력을 내년 1월 8일까지 둬도 되나요?");
  assert.equal(req.rule_fields.find(x=>x.field==="valid")?.action,"keep");
  await f.resolve({vat:set("excluded","부가세 별도 처리로 확정할게요"),valid:set("2027-01-08","견적 효력을 내년 1월 8일까지")});
  f.apply({vat:"excluded"});
  assert.ok(f.holds.fields.valid);
  assert.equal(f.holds.fields.vat,undefined);
});
test("R6-02 수정된 서버 보류 및 오프라인에서도 독립 세금만 적용해요",async()=>{
  for(const offline of [false,true]){
    const f=fixture();
    f.send("부가세 별도 처리로 확정할게요. 견적 효력을 내년 1월 8일까지 둬도 되나요?");
    if(offline) await f.offline();
    else await f.resolve({vat:set("excluded","부가세 별도 처리로 확정할게요"),valid:{action:"uncertain",status:"uncertain",value:null,evidence:"견적 효력을 내년 1월 8일까지 둬도 되나요?"}});
    f.apply({vat:"excluded"});
    assert.ok(f.holds.fields.valid);
  }
});

for(const [word,days] of [["하루",1],["이틀",2],["사흘",3],["나흘",4],["닷새",5],["엿새",6],["이레",7],["여드레",8],["아흐레",9],["열흘",10],["열 사흘",13],["보름",15],["스무 날",20],["두 날",2],["세 일간",3],["14일",14]] as const){
  test("완전한 일수: "+word,async()=>{
    const f=fixture();
    const text="시공 일수는 "+word+"로 정해 주세요.";
    assert.equal(parseQuote(text,opts.today).days,days,text);
    f.send(text);
    await f.offline();
    f.apply({days});
  });
}
for(const [phrase,value] of [
  ["공사기간은 닷새 정도로 잡아주세요",5],
  ["시공은 사나흘로 잡아주세요",4],
  ["공사일수는 두세 날로 정해주세요",3],
  ["작업기간은 닷새나 엿새로 잡아주세요",5],
  ["시공 기간은 1.5일간으로 잡아주세요",5],
  ["시공 일수는 -5일로 정해주세요",5],
  ["공사기간은 12월 5일로 정해주세요",5],
  ["작업일수는 닷새째입니다",5],
  ["시공 일수는 닷새 반으로 잡아주세요",5],
  ["시공은 닷새로 잡을 수 있는지 알려주세요",5],
  ["공사 일수는 닷새로 잡지 마세요",5],
  ["시공 일수는 닷새로 정해주세요. 작업기간은 이틀로 정합니다",5],
  ["시공 일수는 닷새로 잡아주세요. 일수 변경은 취소합니다",5],
  ["가능하면 시공 기간은 닷새로 정해주세요",5],
] as const){
  test("기간 위험 후보 차단: "+phrase,async()=>{
    const f=fixture();
    f.send(phrase+". 부가세는 별도로 정해주세요");
    await f.resolve({days:set(value,phrase),vat:set("excluded","부가세는 별도로 정해주세요")});
    f.apply({vat:"excluded"});
    assert.ok(f.holds.fields.days,phrase);
  });
}
test("기간 값과 서버 값의 불일치, 충돌 대안, 수동 수정 보호",async()=>{
  for(const mode of ["wrong","conflict","manual"]){
    const f=fixture();
    const text="시공은 닷새로 잡아주세요";
    f.send(text);
    if(mode==="manual") f.manual("days",4);
    await f.resolve({days:mode==="conflict"?{action:"uncertain",status:"conflict",evidence:text,alternatives:[
      {action:"set",value:5,source:"llm",evidence:text},{action:"set",value:6,source:"rule",evidence:text}
    ]}:set(mode==="wrong"?6:5,text)});
    f.session.select("days",true);
    f.apply({});
  }
});
test("일수 정정은 독립된 확정 값만 적용하고 과거 후보는 검증해요",async()=>{
  const direct=fixture();
  direct.send("공사기간은 닷새 아니고 이틀로 정해주세요");
  await direct.resolve({days:set(2,"이틀로 정해주세요")});
  direct.apply({days:2});
  for(const mode of ["prior","missing","stale"]){
    const f=fixture();
    if(mode!=="missing"){
      f.send("공사기간은 닷새로 잡아주세요");
      await f.resolve({days:set(5,"닷새로 잡아주세요")});
      if(mode==="stale") f.manual("days",4);
    }
    const text="공사기간은 아까 닷새라고 했는데 이틀로 정정합니다";
    const req=f.send(text);
    assert.equal(req.rule_fields.find(x=>x.field==="days")?.action,"keep");
    await f.resolve({days:set(2,"이틀로 정정합니다")});
    f.apply(mode==="prior"?{days:2}:{});
  }
});
test("일수 이전 제안의 원래 출처와 수동/취소 보호",async()=>{
  const f=fixture();
  const first=f.send("공사기간은 닷새로 잡아주세요");
  await f.resolve({days:set(5,"닷새로 잡아주세요")});
  const next=f.send("공사 일수는 이전 제안 그대로 유지하고 부가세는 별도로 정해주세요");
  assert.equal(next.previous_candidate?.fields.find(x=>x.field==="days")?.value,5);
  await f.resolve({days:{...set(5,"닷새로 잡아주세요"),intent_kind:"retain_previous",value_source:"previous",origin_request_id:first.request_id},vat:set("excluded","부가세는 별도로 정해주세요")});
  f.apply({days:5,vat:"excluded"});
});

test("저장 재생 회귀: 유효기간 철회와 직전 제안으로 유지하는 일수는 독립적이에요",async()=>{
  const f=fixture();
  const first=f.send("공사일수는 13일로 정해주세요. 유효기간은 12월 30일까지로 잡아주세요");
  await f.resolve({days:set(13,"공사일수는 13일로 정해주세요"),valid:set("2026-12-30","유효기간은 12월 30일까지로 잡아주세요")});
  const text="유효기간 변경 요청은 취소하고 공사 기간은 직전 제안으로 유지";
  assert.equal(requestIntent("quote",text).blocked.has("days"),false);
  const next=f.send(text);
  assert.equal(next.previous_candidate?.fields.find(x=>x.field==="days")?.value,13);
  await f.resolve({days:{...set(13,"공사일수는 13일로 정해주세요"),intent_kind:"retain_previous",value_source:"previous",origin_request_id:first.request_id}});
  const third=f.send("부가세는 별도로 정해 주세요");
  assert.equal(third.previous_candidate?.fields.find(x=>x.field==="days")?.value,13);
  await f.resolve({days:{...set(13,"공사일수는 13일로 정해주세요"),intent_kind:"retain_previous",value_source:"previous",origin_request_id:first.request_id},vat:set("excluded","부가세는 별도로 정해 주세요")});
  f.apply({days:13,vat:"excluded"});
  for(const phrase of ["공사기간은 닷새 안으로 잡아주세요","공사기간은 5일안으로 정해주세요"]){
    const g=fixture();
    g.send(phrase);
    await g.resolve({days:set(5,phrase)});
    g.apply({});
    assert.ok(g.holds.fields.days);
  }
});

test("저장 재생 회귀: 단일 새 일수의 재제안·정정은 과거 수량 인용과 구별해요",async()=>{
  for(const [text,value] of [["공사일수는 13일로 다시 제안해주세요",13],["공사기간 12일로 정정할게요",12],["공기는 닷새로 다시 수정해 주세요",5]] as const){
    for(const offline of [false,true]){
      const f=fixture();
      f.send("공사기간은 여드레로 정해주세요");
      await f.resolve({days:set(8,"공사기간은 여드레로 정해주세요")});
      const req=f.send(text);
      assert.equal(req.rule_fields.find(x=>x.field==="days")?.value,value,text);
      if(offline) await f.offline();
      else await f.resolve({days:set(value,text)});
      f.apply({days:value});
    }
  }
  for(const text of ["공사기간 12일로 정정할까요?","공사일수 13일로 다시 제안하지 마세요","공사일수 13일 말고 다시 정할게요","공사일수 13일로 다시 제안해주세요. 일수 변경은 취소합니다"]){
    const f=fixture();
    f.send(text);
    await f.resolve({days:set(13,text)});
    f.apply({});
    assert.ok(f.holds.fields.days,text);
  }
});

for(const alias of ["견적 효력","견적서 효력 기간","유효기한","유효 날짜","유효일","만료일","만료 날짜"]){
  test("유효기간 별칭의 문의/확정/부정: "+alias,async()=>{
    for(const mode of ["inquiry","polite","negative"]){
      const f=fixture();
      const statement=alias+"을 내년 1월 8일까지 "+(mode==="inquiry"?"둬도 되나요":mode==="negative"?"늘리지 마세요":"늘려 주시겠어요?");
      const text="세금은 별도로 정해주세요. "+statement;
      const intent=requestIntent("quote",text);
      assert.equal(intent.requested.has("days"),false,text);
      assert.equal(intent.blocked.has("vat"),false,text);
      assert.equal(intent.blocked.has("valid"),mode!=="polite",text);
      f.send(text);
      await f.resolve({vat:set("excluded","세금은 별도로 정해주세요"),valid:set("2027-01-08",statement)});
      f.apply(mode==="polite"?{vat:"excluded",valid:"2027-01-08"}:{vat:"excluded"});
    }
  });
}
test("별칭을 바꾸어 경쟁 만료일·조건·철회한 후보를 되살리지 않아요",async()=>{
  for(const text of [
    "견적 효력은 12월 30일까지로 정합니다. 만료일은 내년 1월 8일까지로 정합니다",
    "가능하면 견적 효력을 내년 1월 8일까지 연장해 주세요",
    "유효기한은 내년 1월 8일까지로 정합니다. 효력 변경은 취소합니다",
  ]){
    const f=fixture();
    f.send(text);
    await f.resolve({valid:set("2027-01-08",text)});
    f.apply({});
    assert.ok(f.holds.fields.valid);
  }
});
test("동일 절의 조건 연결은 후속 필드에도 적용해요",async()=>{
  const f=fixture();
  const text="부가세가 별도라면 견적 효력을 내년 1월 8일까지 늘려 주세요";
  f.send(text);
  await f.resolve({vat:set("excluded",text),valid:set("2027-01-08",text)});
  f.apply({});
});
test("기간·날짜·추가금 별칭의 독립 요청",async()=>{
  const samples: [AssistForm,AssistCurrent,string,Fields,AssistCurrent,string][]=[
    ["period",period,"방문 시작일은 12월 5일로 확정해 주세요. 방문 접수 마감 일자는 12월 30일로 해도 되나요?",
      {start:set("2026-12-05","방문 시작일은 12월 5일로 확정해 주세요"),end:set("2026-12-30","방문 접수 마감 일자는 12월 30일로")},{start:"2026-12-05"},"end"],
    ["dates",dates,"공사 일자는 12월 10일과 12월 11일로 정해주세요. 투입 인력은 네 명으로 해도 되나요?",
      {days:set(["2026-12-10","2026-12-11"],"공사 일자는 12월 10일과 12월 11일로 정해주세요"),crew:set(4,"투입 인력은 네 명으로")},{days:["2026-12-10","2026-12-11"]},"crew"],
    ["extra",extra,"시공 작업명은 몰딩 보수로 지정해 주세요. 세금 구분은 면세로 해도 되나요?",
      {title:set("몰딩 보수","시공 작업명은 몰딩 보수로 지정해 주세요"),vat:set("exempt","세금 구분은 면세로")},{title:"몰딩 보수"},"vat"]
  ];
  for(const [form,current,text,response,expected,held] of samples){
    const f=fixture(form,current);
    f.send(text);
    await f.resolve(response);
    f.apply(expected);
    assert.ok(f.holds.fields[held],text);
  }
});
test("인원 수사의 전체 토큰을 읽고 단위 조각은 거부해요",()=>{
  for(const [text,n] of [["열 한 명",11],["스물세 명",23],["다섯 명",5],["네 명",4]] as const) assert.equal(readCrew(text).value,n,text);
  for(const text of ["서너 명","열 스무 명","2명분","-3명","두 세 명","3.5명"]) assert.equal(readCrew(text).value,undefined,text);
});
test("고유어 기간은 달력 날짜로 합성하지 않아요",()=>{
  assert.equal(parseDates("작업일은 닷새로 정해주세요",opts.today,opts.baseMonth),null);
  assert.equal(parseQuote("유효기간은 닷새로 정해주세요",opts.today).days,undefined);
  assert.equal(ruleFields("quote","시공 일수는 12월 5일로 잡아주세요",opts).find(x=>x.field==="days")?.action,"keep");
});

test("날짜 이동량과 휴무 일수는 실제 달력 목록의 개수와 구별해요",async()=>{
  const first=fixture("period",{start:"2026-12-05",end:"2026-12-18",excluded:[]});
  const text="12월 24일까지 가능하고 중간에 12일 하루만 쉽니다.";
  const req=first.send(text);
  assert.equal(req.rule_fields.find(x=>x.field==="excluded")?.action,"set");
  await first.resolve({end:set("2026-12-24","12월 24일까지 가능하고"),excluded:set(["2026-12-12"],"중간에 12일 하루만 쉽니다")});
  const second=first.send("아까 쉰다고 한 날은 그대로 두고 21일까지만 할게요");
  assert.deepEqual(second.previous_candidate?.fields.find(x=>x.field==="excluded")?.value,["2026-12-12"]);
  await first.resolve({end:set("2026-12-21","21일까지만 할게요"),excluded:{...set(["2026-12-12"],"중간에 12일 하루만 쉽니다"),intent_kind:"retain_previous",value_source:"previous",origin_request_id:req.request_id}});
  first.apply({end:"2026-12-21",excluded:["2026-12-12"]});

  const f=fixture("dates",{days:["2026-12-09","2026-12-15"],crew:2});
  f.send("이번 작업은 12월 7일, 14일, 21일 이렇게 세 번 나갑니다. 날짜는 이 세 개로 잡아주세요.");
  await f.resolve({days:set(["2026-12-07","2026-12-14","2026-12-21"],"12월 7일, 14일, 21일 이렇게 세 번 나갑니다. 날짜는 이 세 개로 잡아주세요.")});
  const moved="아까 말한 14일만 하루 밀려서 12월 15일로요. 7일이랑 21일은 그대로입니다.";
  f.send(moved);
  await f.resolve({days:{...set(["2026-12-07","2026-12-15","2026-12-21"],moved),source:"rule",value_source:"derived",correction:{kind:"replace",rejected_value:["2026-12-07","2026-12-14","2026-12-21"],rejected_evidence:"14일",accepted_evidence:"12월 15일"}}});
  f.apply({days:["2026-12-07","2026-12-15","2026-12-21"]});
});

test("추가금과 사유·세금 지시문은 새 제목이 아니며 명시 인용 제목은 보존해요",async()=>{
  for(const offline of [false,true]){
    const cases: [string,Fields,AssistCurrent][]=[
      ["추가 작업 비용은 48만 2천 원, 별도 세금 방식으로 입력 부탁드려요.",{amount:set(482000,"48만 2천 원"),vat:set("excluded","별도 세금 방식"),title:set("추가 작업 비용은","추가 작업 비용은")},{amount:482000,vat:"excluded"}],
      ["추가 사유는 접합부 균열로 지정합니다.",{reason:set("접합부 균열","추가 사유는 접합부 균열로 지정합니다"),title:set("추가 사유는 접합부 균열로 지정합니다.","추가 사유는 접합부 균열로 지정합니다.")},{reason:"접합부 균열"}],
      ["확인되면 추가 사유를 단열재 손상으로 기입할 생각입니다. 부가세 별도로 확정할게요.",{reason:set("단열재 손상","추가 사유를 단열재 손상으로 기입할 생각입니다"),title:set("확인되면 추가 사유를 단열재 손상으로 기입할 생각입니다","확인되면 추가 사유를 단열재 손상으로 기입할 생각입니다"),vat:set("excluded","부가세 별도로 확정할게요")},{vat:"excluded"}],
      ["추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다. 추가금 27만원으로 확정합니다.",{title:set("사흘간 건조 후 보수","추가 작업명은 '사흘간 건조 후 보수'로 지정 바랍니다"),amount:set(270000,"27만원")},{title:"사흘간 건조 후 보수",amount:270000}],
    ];
    for(const [text,response,expected] of cases){
      const f=fixture("extra",extra);
      f.send(text);
      if(offline) await f.offline();
      else await f.resolve(response);
      f.apply(expected);
    }
    const f=fixture("extra",extra);
    f.send("작업명은 몰딩 보수로 지정해주세요");
    await f.resolve({title:set("몰딩 보수","작업명은 몰딩 보수로 지정해주세요")});
    f.manual("title","수동 제목");
    const text="작업명은 이전 후보를 쓰고 세금은 별도로 처리해 주세요.";
    const req=f.send(text);
    assert.equal(req.previous_candidate?.fields.some(x=>x.field==="title")??false,false);
    if(offline) await f.offline();
    else await f.resolve({title:set("이전 후보를 쓰고","작업명은 이전 후보를 쓰고"),vat:set("excluded","세금은 별도로 처리해 주세요")});
    f.apply({vat:"excluded"});
    assert.ok(f.holds.fields.title);
  }
});

test("달력의 개수·날짜 목록을 대조하며 무관한 마감과 인원은 보존해요",async()=>{
  for(const offline of [false,true]){
    const cases: [AssistForm,AssistCurrent,string,Fields,AssistCurrent,string][]=[
      ["period",period,"방문 불가일은 사흘로 해주세요. 접수 마감일은 12월 29일로 확정합니다.",{excluded:set(["2026-12-05","2026-12-06","2026-12-07"],"방문 불가일은 사흘로 해주세요"),end:set("2026-12-29","접수 마감일은 12월 29일로 확정합니다")},{end:"2026-12-29"},"excluded"],
      ["period",period,"방문 제외일은 사흘로 잡겠습니다. 12월 10일과 20일입니다.",{excluded:set(["2026-12-10","2026-12-20"],"12월 10일과 20일입니다")},{},"excluded"],
      ["dates",dates,"작업일은 사흘로 정하고 날짜는 12월 4일과 20일로 등록해 주세요. 작업자는 3명으로 확정합니다.",{days:set(["2026-12-04","2026-12-20"],"날짜는 12월 4일과 20일로 등록해 주세요"),crew:set(3,"작업자는 3명으로 확정합니다")},{crew:3},"days"],
      ["quote",quote,"공사에 사흘에서 나흘 정도 잡을까 합니다. 견적 총액은 412만 원으로 확정합니다.",{days:set(3,"공사에 사흘에서 나흘 정도 잡을까 합니다"),amount:set(4120000,"견적 총액은 412만 원으로 확정합니다")},{amount:4120000},"days"],
    ];
    for(const [form,current,text,response,expected,held] of cases){
      const f=fixture(form,current);
      f.send(text);
      if(offline) await f.offline();
      else await f.resolve(response);
      f.apply(expected);
      assert.ok(f.holds.fields[held],text);
      if(form==="period") assert.equal(f.holds.fields.start,undefined,text);
    }
  }
  for(const form of ["period","dates"] as const){
    const f=fixture(form,form==="period"?period:dates);
    const field=form==="period"?"excluded":"days";
    const text=(form==="period"?"방문 제외일":"작업일")+"은 이틀로 잡겠습니다. 12월 10일과 20일입니다.";
    f.send(text);
    await f.resolve({[field]:set(["2026-12-10","2026-12-20"],text)});
    f.apply({[field]:["2026-12-10","2026-12-20"]});
  }
});

test("정중한 연장 요청과 일정 조건의 실제 대상은 분리해요",async()=>{
  for(const [form,current,field,value,prefix] of [
    ["quote",quote,"valid","2026-12-26","견적서 효력을 12월26일까지 연장해 "],
    ["period",period,"end","2026-12-30","실측 마감 날짜를 12월30일로 늘려 "],
  ] as const){
    for(const [suffix,allowed] of [["주실래요?",true],["주실 수 있나요?",false],["줄 수 있는지 알려주세요",false]] as const){
      const f=fixture(form,current);
      const text=prefix+suffix;
      f.send(text);
      await f.resolve({[field]:set(value,text)});
      f.apply(allowed?{[field]:value}:{});
    }
  }
  const f=fixture("dates",dates);
  const text="일정이 맞으면 투입 인원은 세 명으로 할 생각입니다. 시공일은 12월8일과21일로 정합니다.";
  f.send(text);
  await f.resolve({crew:set(3,"투입 인원은 세 명으로 할 생각입니다"),days:set(["2026-12-08","2026-12-21"],"시공일은 12월8일과21일로 정합니다")});
  f.apply({days:["2026-12-08","2026-12-21"]});
  assert.ok(f.holds.fields.crew);
  const conditional=fixture("dates",dates);
  const phrase="시공일을 12월8일과21일로 바꿀 수 있으면 투입 인원은 세 명으로 할 생각입니다.";
  conditional.send(phrase);
  await conditional.resolve({crew:set(3,phrase),days:set(["2026-12-08","2026-12-21"],phrase)});
  conditional.apply({});
});

test("저장 재생 회귀: 연도를 포함한 유효기간과 소요일은 독립 필드예요",async()=>{
  for(const [text,value] of [["견적 유효기간은 2026년 12월 27일까지로 잡아주시고요. 공사 소요일은 7일로 적어주세요.",7],["시공 소요 일수는 이레로 적어주세요. 견적 효력은 2026년 12월 27일까지로 정해주세요.",7]] as const){
    for(const offline of [false,true]){
      const f=fixture();
      const req=f.send(text);
      assert.equal(req.rule_fields.find(x=>x.field==="days")?.value,value,text);
      assert.equal(req.rule_fields.find(x=>x.field==="valid")?.value,"2026-12-27",text);
      if(offline) await f.offline();
      else await f.resolve({days:set(value,text),valid:set("2026-12-27",text)});
      f.apply({days:value,valid:"2026-12-27"});
    }
  }
  const f=fixture();
  const text="견적 유효기간은 2026년 12월 27일까지로 잡아주세요. 소요일은 7일이면 되나요?";
  f.send(text);
  await f.resolve({days:set(7,text),valid:set("2026-12-27",text)});
  f.apply({valid:"2026-12-27"});
  assert.ok(f.holds.fields.days);
});
