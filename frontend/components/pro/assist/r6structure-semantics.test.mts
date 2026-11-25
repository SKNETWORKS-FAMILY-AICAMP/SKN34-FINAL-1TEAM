// R6 구조 개선: 저장된 quote-04 응답과 새 독립 대조를 실제 세션에 재생해요.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";
import { readQuoteDuration } from "./parse.ts";
import { requestIntent } from "./requestIntent.ts";
import { classifyDurationQuantity } from "./quantity.ts";

const opts: RuleOptions = {today:"2026-11-25",baseMonth:"2026-12-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const resource="00000000-0000-4000-8000-000000000179";

function fixture(form:AssistForm, initial:AssistCurrent) {
  let current=structuredClone(initial), pending=false, request:AssistRequest;
  const manualVersions:Record<string,number>={}, holds:Record<string,string>={};
  let respond:(r:AssistRequest)=>Partial<CandidateField>[];
  let responseWarnings:string[]=[];
  const session=new AssistSession(form,resource,async r=>{
    request=structuredClone(r);
    const fields=respond(r);
    return {form,request_id:r.request_id,source:"llm",warnings:responseWarnings,
      fields:formKeys(form).map(field=>({field,action:"keep",status:"valid",value:null,evidence:"",source:"rule",
        intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id,alternatives:[],
        correction:null,reason_code:null,
        ...fields.find(x=>x.field===field)}))};
  });
  const getLive=()=>({current:structuredClone(current),manualVersions:{...manualVersions}});
  async function send(text:string, reply:typeof respond, warnings:string[]=[]) {
    respond=reply;
    responseWarnings=warnings;
    let done!:()=>void;
    const ready=new Promise<void>(resolve=>{done=resolve;});
    const off=session.subscribe(v=>{if(v.phase==="ready"&&v.utterance===text)done();});
    assert(session.send(text,current,opts,delta=>{
      if(typeof delta.pending==="boolean")pending=delta.pending;
      for(const [field,value] of Object.entries(delta.fieldHolds??{})){
        if(typeof value==="string")holds[field]=value;
        else if(value===null)delete holds[field];
      }
    },getLive));
    assert(pending);
    assert.equal(session.apply(current,()=>assert.fail("응답 전 적용")),false);
    await ready; off();
    assert.equal(pending,false);
    return request!;
  }
  function manual(field:string,value:AssistValue){
    current[field]=value;
    manualVersions[field]=(manualVersions[field]??0)+1;
    delete holds[field];
  }
  function field(name:string){return session.view.response!.fields.find(f=>f.field===name)!;}
  function apply(expected:AssistCurrent){
    const selected=Object.keys(expected);
    for(const name of formKeys(form)) session.select(name,selected.includes(name));
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,v=>{patch=structuredClone(v);Object.assign(current,v);}),selected.length>0);
    assert.deepEqual(patch,expected);
  }
  return {session,send,manual,field,apply,holds,get current(){return current;}};
}

function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):Partial<CandidateField>{
  return {field,action:"set",status:"valid",value,evidence,source:"llm",alternatives:[],
    intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id};
}
function retainCurrent(r:AssistRequest,field:string,evidence:string):Partial<CandidateField>{
  return {field,action:"keep",status:"valid",value:null,evidence,source:"rule",alternatives:[],
    intent_kind:"retain_current",value_source:"current",origin_request_id:r.request_id};
}
function carry(r:AssistRequest,field:string):Partial<CandidateField>{
  const p=r.previous_candidate, old=p?.fields.find(f=>f.field===field);
  assert(p&&old);
  return {...old,status:"valid",alternatives:[],source:p.sources![field],origin_request_id:p.origins![field],
    intent_kind:"retain_previous",value_source:"previous"};
}

// 원본 qa179-r6future-live-results.json의 FUT14-quote-04 T1/T2 server 필드.
// 동적 세션 요청 ID만 새 ID로 치환하고 금액·수량·근거·출처·의도를 그대로 재생해요.
test("FUT14-quote-04 저장 응답: 금액 철회와 기존 8일 후보 적용",async()=>{
  const initial={amount:3260000,vat:"included",days:4,valid:"2027-01-27"};
  const f=fixture("quote",initial);
  const first=await f.send("총 견적 392만원에 공사 일수는 8일로 후보를 잡아 주세요.",r=>[
    set(r,"amount",3920000,"총 견적 392만원"),set(r,"days",8,"공사 일수는 8일"),
  ]);
  assert.deepEqual(f.current,initial);
  assert.equal(f.field("days").value,8);
  assert.ok(f.holds.amount&&f.holds.days);
  const t2="금액 변경은 철회합니다. 소요일은 직전에 제안한 일수 그대로 반영해 주세요.";
  await f.send(t2,r=>{
    assert.equal(r.previous_candidate?.fields.find(v=>v.field==="days")?.value,8);
    return [retainCurrent(r,"amount","금액 변경은 철회합니다"),{
      ...set(r,"days",8,"공사 일수는 8일"),intent_kind:"retain_previous",value_source:"previous",origin_request_id:first.request_id,
    }];
  },["원문 근거나 업무 조건을 확인할 수 없는 후보가 있어요."]);
  assert.equal(f.field("days").status,"valid");
  assert.equal(f.field("days").origin_request_id,first.request_id);
  assert.equal(f.field("days").evidence,"공사 일수는 8일");
  assert.equal(f.holds.amount,undefined);
  f.apply({days:8});
  assert.deepEqual(f.holds,{});
  assert.deepEqual(f.current,{...initial,days:8});
});

for(const text of [
  "공사 일수는 앞서 말씀드린 일수 그대로 유지해 주세요.",
  "공사 기간은 직전에 입력한 일수로 유지합니다.",
  "공사 일수는 이전에 제시한 일수 그대로 사용하겠습니다.",
  "공사 일수는 이전에 제안한 일수 그대로 반영해 주세요.",
]){
  test("일수 명사 참조와 수량은 달라요: "+text,()=>{
    assert.deepEqual(readQuoteDuration(text),{ambiguous:false});
    assert.equal(requestIntent("quote",text).blocked.has("days"),false);
  });
}
for(const text of [
  "공사 일수는 한 일로 정해 주세요.",
  "공사 일수는 1.5일로 정해 주세요.",
  "공사 일수는 -5일로 정해 주세요.",
  "공사 일수는 두세 날로 정해 주세요.",
  "공사일수는닷새로 정해 주세요.",
  "시공은닷새로 정해 주세요.",
  "공사 일수는 닷새째입니다.",
  "공사 일수는 12월 5일로 정해 주세요.",
  "공사 일수는 3일과 5일 중에 정해 주세요.",
]){
  for(const mode of ["new","previous"] as const)test("실제 위험 수량은 보류: "+mode+" "+text,async()=>{
    assert(readQuoteDuration(text).ambiguous);
    const f=fixture("quote",{amount:3260000,vat:"included",days:4,valid:"2027-01-27"});
    await f.send("공사 일수는 8일로 정해 주세요.",r=>[set(r,"days",8)]);
    await f.send(text,r=>[mode==="new"?set(r,"days",8):carry(r,"days")]);
    assert.notEqual(f.field("days").status,"valid");
    f.apply({});
  });
}

for(const valueText of ["6일로 바꿔 주세요.","여드레로 다시 정해 주세요.","두세 날로 정해 주세요."]){
  for(const mode of ["new","previous"] as const)test("앞 칸의 수량만 이어진 경쟁 절: "+mode+" "+valueText,async()=>{
    const f=fixture("quote",{amount:3260000,vat:"included",days:4,valid:"2027-01-27"});
    await f.send("공사 일수는 8일로 정해 주세요.",r=>[set(r,"days",8)]);
    const text="공사 일수는 이전 후보 그대로, "+valueText;
    await f.send(text+" 부가세는 별도로 정해 주세요.",r=>[
      mode==="previous"?carry(r,"days"):set(r,"days",valueText.startsWith("6")?6:8,valueText),
      set(r,"vat","excluded","부가세는 별도로 정해 주세요."),
    ]);
    assert.notEqual(f.field("days").status,"valid");
    f.apply({vat:"excluded"});
    assert.ok(f.holds.days);
  });
}

test("이전값 명사 참조에 새 출처를 붙인 일수는 적용하지 않아요",async()=>{
  const f=fixture("quote",{amount:3260000,vat:"included",days:4,valid:"2027-01-27"});
  await f.send("공사 일수는 8일로 정해 주세요.",r=>[set(r,"days",8)]);
  const text="소요일은 직전에 제안한 일수 그대로 반영해 주세요.";
  await f.send(text,r=>[set(r,"days",8,text)]);
  assert.notEqual(f.field("days").status,"valid");
  f.apply({});
});

type Example={form:AssistForm;current:AssistCurrent;field:string;value:AssistValue;first:string;reference:string;cancel:string;conditional:string;currentText:string;other:string;otherField:string;otherValue:AssistValue};
const examples:Example[]=[
  {form:"quote",current:{amount:3260000,vat:"included",days:4,valid:"2027-01-27"},field:"days",value:8,
    first:"공사 일수는 여드레로 정해 주세요.",reference:"소요일은 직전에 제안한 일수 그대로 반영해 주세요.",
    cancel:"일수 변경은 취소합니다.",conditional:"조건부로 소요일은 직전에 제안한 일수 그대로 반영해 주세요.",currentText:"공사 일수는 현재 입력값 그대로 유지합니다.",
    other:"부가세는 별도로 정해 주세요.",otherField:"vat",otherValue:"excluded"},
  {form:"dates",current:{days:["2026-12-08","2026-12-12"],crew:2},field:"crew",value:4,
    first:"작업 인원은 네 명으로 정해 주세요.",reference:"작업 인원은 직전 제안 그대로 유지해 주세요.",
    cancel:"인원 변경은 취소합니다.",conditional:"조건부로 작업 인원은 직전 제안 그대로 유지해 주세요.",currentText:"작업 인원은 현재 입력값 그대로 유지합니다.",
    other:"작업일은 12월 10일과 16일로 정해 주세요.",otherField:"days",otherValue:["2026-12-10","2026-12-16"]},
  {form:"period",current:{start:"2026-12-02",end:"2026-12-24",excluded:[]},field:"start",value:"2026-12-05",
    first:"시작일은 12월 5일로 정해 주세요.",reference:"시작일은 직전 제안 그대로 유지해 주세요.",
    cancel:"시작일 변경은 취소합니다.",conditional:"조건부로 시작일은 직전 제안 그대로 유지해 주세요.",currentText:"시작일은 현재 입력값 그대로 유지합니다.",
    other:"종료일은 12월 27일로 정해 주세요.",otherField:"end",otherValue:"2026-12-27"},
  {form:"extra",current:{title:"기존 보강",reason:"기존 손상",amount:200000,vat:"included"},field:"amount",value:320000,
    first:"추가 금액은 32만 원으로 정해 주세요.",reference:"금액은 직전 제안 그대로 유지해 주세요.",
    cancel:"금액 변경은 취소합니다.",conditional:"조건부로 금액은 직전 제안 그대로 유지해 주세요.",currentText:"금액은 현재 입력값 그대로 유지합니다.",
    other:"부가세는 별도로 정해 주세요.",otherField:"vat",otherValue:"excluded"},
];
for(const e of examples){
  for(const mode of ["previous","cancel","current","conditional","forged-origin","forged-evidence","forged-source","manual"] as const){
    test(e.form+" 독립 계승/철회/출처: "+mode,async()=>{
      const f=fixture(e.form,e.current);
      const first=await f.send(e.first,r=>[set(r,e.field,e.value)]);
      assert.equal(f.field(e.field).status,"valid");
      if(mode==="manual")f.manual(e.field,e.current[e.field]);
      const ref=mode==="cancel"?e.reference+" "+e.cancel:mode==="current"?e.currentText:mode==="conditional"?e.conditional:e.reference;
      await f.send(ref+" "+e.other,r=>{
        if(mode==="manual")assert(!r.previous_candidate?.fields.some(f=>f.field===e.field));
        const prev=mode==="manual"?{...set(r,e.field,e.value,e.first),intent_kind:"retain_previous" as const,value_source:"previous" as const,origin_request_id:first.request_id}:carry(r,e.field);
        if(mode==="forged-origin")prev.origin_request_id=r.request_id;
        if(mode==="forged-evidence")prev.evidence=e.first+" 추가";
        if(mode==="forged-source")prev.source="rule";
        if(mode==="current")return [retainCurrent(r,e.field,e.currentText.slice(0,-1)),set(r,e.otherField,e.otherValue,e.other)];
        return [prev,set(r,e.otherField,e.otherValue,e.other)];
      });
      const expected:AssistCurrent={[e.otherField]:e.otherValue};
      if(mode==="previous"){
        assert.equal(f.field(e.field).origin_request_id,first.request_id);
        assert.deepEqual(f.field(e.field).value,e.value);
        expected[e.field]=e.value;
      } else if(mode==="current"){
        assert.equal(f.field(e.field).action,"keep");
        assert.equal(f.holds[e.field],undefined);
      } else assert.notEqual(f.field(e.field).status,"valid");
      f.apply(expected);
    });
  }
}

// 일반 횟수·대상 수량과 일수/시간 근삿수사를 구분해요.
for(const text of ["한두 번 확인합니다.","두세 차례 검수합니다.","서너 곳을 점검합니다.","대여섯 사람과 상의합니다.","예닐곱 장을 준비합니다."]){
  test("일수 아닌 근삿수사: "+text,()=>{
    assert.deepEqual(classifyDurationQuantity(text),{tokens:[],mentioned:false,risk:false});
  });
}
for(const detail of ["한두 번의 확인을 거친","두세 차례의 검수를 포함한","서너 곳의 작업을 포함한","대여섯 사람의 작업을 포함한","예닐곱 장의 자료 확인을 포함한"]){
  test("정확한 공사 일수와 별개 일반 수량: "+detail,async()=>{
    const text="공사 일수는 "+detail+" 6일로 확정합니다.";
    assert.deepEqual(readQuoteDuration(text),{value:6,ambiguous:false});
    assert(!requestIntent("quote",text).blocked.has("days"));
    const f=fixture("quote",examples[0].current);
    await f.send(text,r=>[set(r,"days",6,"6일")]);
    f.apply({days:6});
  });
}
for(const phrase of ["한두 날","두세 일","서너 주","대여섯 달","예닐곱 시간","한두 정도","두세","하루 이틀","사나흘","6일 또는 한두 날","6일에 두세 시간"]){
  test("일수/시간 위험 수량 유지: "+phrase,async()=>{
    const text="공사 일수는 "+phrase+"로 정합니다.";
    assert(classifyDurationQuantity(text).risk,text);
    const f=fixture("quote",examples[0].current);
    await f.send(text,r=>[set(r,"days",6)]);
    f.apply({});
  });
}
test("일반 횟수만 있는 일수 요청은 새 수량의 근거가 아니에요",async()=>{
  const f=fixture("quote",examples[0].current);
  const text="공사 일수는 두세 차례의 검수를 거쳐 정합니다.";
  await f.send(text,r=>[set(r,"days",6)]);
  f.apply({});
});

for(const mode of ["same","different","question","conditional","cancel","forged-proof","stale","carry"] as const){
  test("완결된 이전 유지와 별도 확정의 동일값 재확인: "+mode,async()=>{
    const f=fixture("quote",examples[0].current);
    const seed="공사 일수는 8일로 정합니다.";
    await f.send(seed,r=>[set(r,"days",8)]);
    if(mode==="stale") f.manual("days",5);
    const reference="공사 일수는 직전 후보 그대로 유지해 주세요.";
    const assignment=mode==="different"?"공사 일수는 6일로 확정합니다.":
      mode==="question"?"공사 일수는 8일로 확정할까요?":
      mode==="conditional"?"가능하면 공사 일수는 8일로 확정합니다.":
      "공사 일수는 8일로 확정합니다.";
    const text=reference+" "+assignment+(mode==="cancel"?" 공사 일수 변경은 취소합니다.":"");
    const r2=await f.send(text+" 부가세는 별도로 정합니다.",r=>[
      mode==="carry"?carry(r,"days"):set(r,"days",mode==="different"?6:8,mode==="forged-proof"?reference:assignment),
      set(r,"vat","excluded","부가세는 별도로 정합니다.")
    ]);
    if(mode==="same"){
      assert.equal(f.field("days").origin_request_id,r2.request_id);
      f.apply({days:8,vat:"excluded"});
    }else{
      assert.notEqual(f.field("days").status,"valid");
      f.apply({vat:"excluded"});
    }
  });
}

// 저장된 live44 S5-P3 T1/T2의 실제 서버 응답을 세션에 재생해요.
// 목록 원소의 보호와 전체 목록의 유지 지시는 서로 달라요.
const protectedPeriodSeed="실측 기간은 12월 5일부터 27일까지로 하고 12일과 19일은 제외해 주세요.";
const protectedPeriodEdit="앞서 제안한 제외일 12일은 건드리지 말고, 제외일 19일만 20일로 바꿔 주세요. 시작일과 종료일은 앞서 제안한 그대로 유지해 주세요.";
const protectedBefore=["2026-12-12","2026-12-19"];
const protectedAfter=["2026-12-12","2026-12-20"];
for(const mode of ["saved","removed-protected","wrong-old-proof","wrong-new-proof","missing-relation","stale","whole-list-keep","cancel","question"] as const){
  test("S5-P3 원소 보호와 별도 날짜 교체: "+mode,async()=>{
    const f=fixture("period",{start:"2026-12-02",end:"2026-12-24",excluded:[]});
    const r1=await f.send(protectedPeriodSeed,r=>[
      set(r,"start","2026-12-05","12월 5일부터"),
      set(r,"end","2026-12-27","27일까지"),
      set(r,"excluded",protectedBefore,"12일과 19일은 제외해 주세요"),
    ]);
    assert.deepEqual(f.field("excluded").value,protectedBefore);
    if(mode==="stale")f.manual("excluded",["2026-12-12","2026-12-18"]);
    const text=mode==="whole-list-keep"?protectedPeriodEdit+" 제외일 목록은 앞서 제안한 그대로 유지해 주세요.":
      mode==="cancel"?protectedPeriodEdit+" 제외일 변경은 취소합니다.":
      mode==="question"?protectedPeriodEdit.replace("20일로 바꿔 주세요.","20일로 바꿀까요?"):protectedPeriodEdit;
    const r2=await f.send(text,r=>{
      const changed:Partial<CandidateField>={
        ...set(r,"excluded",mode==="removed-protected"?["2026-12-19","2026-12-20"]:protectedAfter,text),
        source:"rule",value_source:"derived",
        correction:{kind:"replace",rejected_value:protectedBefore,
          rejected_evidence:mode==="wrong-old-proof"?"12일":"19일",
          accepted_evidence:mode==="wrong-new-proof"?"12일":"20일"},
      };
      if(mode==="missing-relation")delete changed.correction;
      return [carry(r,"start"),carry(r,"end"),changed];
    });
    assert.equal(f.field("start").origin_request_id,r1.request_id);
    assert.equal(f.field("end").origin_request_id,r1.request_id);
    if(mode==="saved"){
      assert.equal(f.field("excluded").status,"valid");
      assert.equal(f.field("excluded").origin_request_id,r2.request_id);
      assert.deepEqual(f.field("excluded").value,protectedAfter);
      f.apply({start:"2026-12-05",end:"2026-12-27",excluded:protectedAfter});
      assert.deepEqual(f.holds,{});
    }else{
      assert.notEqual(f.field("excluded").status,"valid");
      f.apply({start:"2026-12-05",end:"2026-12-27"});
      assert.ok(f.holds.excluded);
    }
  });
}
