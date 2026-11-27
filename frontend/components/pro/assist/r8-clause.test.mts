// R8: 공통 절 소유권과 독립 필드 취소/유지/조건의 대조군.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";
import { requestIntent, scopedFieldParts } from "./requestIntent.ts";
import { ruleFields, ruleResponse } from "./rules.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2027-01-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const currents:Record<AssistForm,AssistCurrent>={
  quote:{amount:3260000,vat:"included",days:4,valid:"2027-01-27"},
  extra:{title:"기존 보강",reason:"기존 손상",amount:320000,vat:"included"},
  dates:{days:["2027-01-08","2027-01-12"],crew:2},
  period:{start:"2027-01-06",end:"2027-01-29",excluded:["2027-01-14","2027-01-22"]},
};
type PartialField=Partial<CandidateField>;
function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):PartialField{
  return {field,action:"set",status:"valid",value,evidence,source:"llm",intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,alternatives:[]};
}
function keep(r:AssistRequest,field:string,evidence:string):PartialField{
  return {field,action:"keep",status:"valid",value:null,evidence,source:"rule",intent_kind:"retain_current",value_source:"current",origin_request_id:r.request_id,alternatives:[]};
}
function hold(r:AssistRequest,field:string,evidence=r.text):PartialField{
  return {field,action:"uncertain",status:"uncertain",value:null,evidence,source:"llm",intent_kind:"ambiguous",origin_request_id:r.request_id,reason_code:"ambiguous_intent",alternatives:[]};
}
function fixture(form:AssistForm){
  let current=structuredClone(currents[form]),pending=false;
  const versions:Record<string,number>={},holds:Record<string,string>={};
  let reply:(r:AssistRequest)=>PartialField[],offline=false;
  const session=new AssistSession(form,"00000000-0000-4000-8000-000000000179",async r=>{
    if(offline)throw new Error("offline");
    const fields=reply(r);
    return {form,request_id:r.request_id,source:"llm",warnings:[],
      fields:formKeys(form).map(field=>({field,action:"keep",status:"valid",value:null,evidence:"",source:"rule",
        intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id,alternatives:[],correction:null,reason_code:null,
        ...fields.find(f=>f.field===field)}))};
  });
  async function send(text:string,respond:typeof reply,fail=false){
    reply=respond;offline=fail;
    let done!:()=>void;
    const ready=new Promise<void>(resolve=>{done=resolve;});
    const off=session.subscribe(v=>{if(v.phase==="ready"&&v.utterance===text)done();});
    assert(session.send(text,current,opts,delta=>{
      if(typeof delta.pending==="boolean")pending=delta.pending;
      for(const [f,v] of Object.entries(delta.fieldHolds??{})){
        if(typeof v==="string")holds[f]=v;
        else if(v===null)delete holds[f];
      }
    },()=>({current:structuredClone(current),manualVersions:{...versions}})));
    assert(pending);assert.equal(session.apply(current,()=>assert.fail("응답 전 적용")),false);
    await ready;off();assert(!pending);
  }
  function field(key:string){return session.view.response!.fields.find(f=>f.field===key)!;}
  function manual(key:string,value:AssistValue){current[key]=value;versions[key]=(versions[key]??0)+1;delete holds[key];}
  function apply(expected:AssistCurrent){
    for(const key of formKeys(form))session.select(key,Object.hasOwn(expected,key));
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,v=>{patch=structuredClone(v);Object.assign(current,v);}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);
  }
  return {send,field,manual,apply,holds,get current(){return current;}};
}

test("R7 quote09 T1 보류 뒤 T2 수정 서버 정상 후보 및 취소 해제",async()=>{
  const f=fixture("quote");
  const t1="견적서 만료일을 1월 18일로 잡는 것이 가능할지 모르겠네요.";
  await f.send(t1,r=>[hold(r,"valid")]);
  assert.ok(f.holds.valid);
  const cancel="유효기간 수정은 없던 일로 하고";
  const assign="견적 가격은 354만 원으로 확정해 주세요.";
  await f.send(cancel+" "+assign,r=>[keep(r,"valid",cancel),set(r,"amount",3540000,assign.slice(0,-1))]);
  f.apply({amount:3540000});
  assert.equal(f.current.valid,currents.quote.valid);
  assert.deepEqual(f.holds,{});
});

const independent=[
  {form:"quote",other:"valid",cancel:"유효기간 변경은 취소하고",assign:"견적 금액은 1천2백만 원으로 확정해 주세요.",field:"amount",value:12000000,rule:true},
  {form:"quote",other:"days",cancel:"공사 기간은 지금 값 그대로 두고",assign:"견적 가격은 삼백사십만 원으로 확정해 주세요.",field:"amount",value:3400000,rule:false},
  {form:"quote",other:"vat",cancel:"세금 방식 변경은 취소하고",assign:"공사 일수는 닷새로 확정해 주세요.",field:"days",value:5,rule:true},
  {form:"quote",other:"days",cancel:"공사 일수 변경은 취소하고",assign:"견적서 만료일은 1월 18일로 확정해 주세요.",field:"valid",value:"2027-01-18",rule:true},
  {form:"extra",other:"title",cancel:"제목 변경은 취소하고",assign:"추가 공사 금액은 1백2십만 원으로 확정해 주세요.",field:"amount",value:1200000,rule:true},
  {form:"extra",other:"reason",cancel:"사유 변경은 취소하고",assign:"세금 방식은 별도로 확정해 주세요.",field:"vat",value:"excluded",rule:true},
  {form:"extra",other:"vat",cancel:"세금 방식 변경은 취소하고",assign:"항목명은 벽체 보강으로 정해 주세요.",field:"title",value:"벽체 보강",rule:true},
  {form:"dates",other:"days",cancel:"작업 일정 변경은 취소하고",assign:"작업 인원은 4명으로 정해 주세요.",field:"crew",value:4,rule:true},
  {form:"dates",other:"crew",cancel:"작업 인원 변경은 취소하고",assign:"작업 일정은 1월 14일과 21일로 정해 주세요.",field:"days",value:["2027-01-14","2027-01-21"],rule:true},
  {form:"period",other:"excluded",cancel:"제외일 변경은 취소하고",assign:"방문 가능 기간 종료일은 2월 3일로 정해 주세요.",field:"end",value:"2027-02-03",rule:true},
] as const;
for(const c of independent){
  const text=c.cancel+" "+c.assign;
  test("공통 절 독립 후보 "+c.form+" "+text,async()=>{
    const f=fixture(c.form);
    assert(!requestIntent(c.form,text).blocked.has(c.field));
    if(c.rule)assert.deepEqual(ruleFields(c.form,text,opts).find(x=>x.field===c.field)?.value,c.value);
    await f.send(text,r=>[keep(r,c.other,c.cancel),set(r,c.field,c.value as AssistValue,c.assign.slice(0,-1))]);
    f.apply({[c.field]:c.value as AssistValue});
    assert.equal(f.holds[c.other],undefined);
  });
}

const unsafe=[
  {form:"quote",field:"amount",value:12000000,text:"견적 금액은 1천2백만 원으로 정하고 금액 변경은 취소합니다."},
  {form:"quote",field:"amount",value:12000000,text:"유효기간 변경이 취소되면 견적 금액은 1천2백만 원으로 정해 주세요."},
  {form:"quote",field:"amount",value:3400000,text:"견적 가격은 삼백사십만 원으로 확정해 주세요. 잠깐, 취소할게요."},
  {form:"quote",field:"days",value:5,text:"공사 일수는 닷새로 정하고 공사 기간 변경은 취소합니다."},
  {form:"quote",field:"valid",value:"2027-01-18",text:"견적서 만료일은 1월 18일로 정하고 유효기간 변경은 취소합니다."},
  {form:"extra",field:"amount",value:1200000,text:"제목 변경을 취소하면 추가 공사 금액은 1백2십만 원으로 정해 주세요."},
  {form:"extra",field:"vat",value:"excluded",text:"세금 방식은 별도로 정하고 부가세 변경은 취소합니다."},
  {form:"extra",field:"title",value:"벽체 보강",text:"항목명은 벽체 보강으로 정하고 제목 변경은 취소합니다."},
  {form:"dates",field:"crew",value:4,text:"작업 인원은 4명으로 정하고 인원 변경은 취소합니다."},
  {form:"dates",field:"days",value:["2027-01-14","2027-01-21"],text:"작업 인원이 확정되면 작업 일정은 1월 14일과 21일로 정해 주세요."},
  {form:"period",field:"end",value:"2027-02-03",text:"제외일 변경을 취소하면 방문 가능 기간 종료일은 2월 3일로 정해 주세요."},
] as const;
for(const c of unsafe)test("위험 절 보존 "+c.form+" "+c.text,async()=>{
  const f=fixture(c.form);
  assert.equal(ruleFields(c.form,c.text,opts).find(x=>x.field===c.field)?.action,"keep");
  await f.send(c.text,r=>[set(r,c.field,c.value as AssistValue)]);
  f.apply({});
  assert.notEqual(f.field(c.field).status,"valid");
});

test("다른 필드의 날짜/범위는 한글 금액의 범위가 아니에요",async()=>{
  const text="견적 금액은 삼백사십만 원으로 확정하고 유효기간은 1월 18일까지 정해 주세요.";
  const f=fixture("quote");
  await f.send(text,r=>[set(r,"amount",3400000,"삼백사십만 원"),set(r,"valid","2027-01-18","유효기간은 1월 18일까지")]);
  f.apply({amount:3400000,valid:"2027-01-18"});
});

test("복합 금액 문의와 독립 일수의 규칙 대조",async()=>{
  const f=fixture("quote");
  const text="견적 금액은 1천2백만 원으로 할까요? 공사 일수는 닷새로 정해 주세요.";
  await f.send(text,r=>[set(r,"amount",12000000),set(r,"days",5)]);
  assert.notEqual(f.field("amount").status,"valid");
  f.apply({days:5});assert.ok(f.holds.amount);
});

test("독립 절 수정 뒤 수동 금액이 보존돼요",async()=>{
  const f=fixture("quote");
  const text="유효기간 수정은 없던 일로 하고 견적 가격은 354만 원으로 확정해 주세요.";
  await f.send(text,r=>[set(r,"amount",3540000,"354만 원"),keep(r,"valid","유효기간 수정은 없던 일로 하고")]);
  f.manual("amount",3330000);
  f.apply({});assert.equal(f.current.amount,3330000);
});

// R8 공통 연결 조건 감사 후 추가 동결한 독립 대조군.
const conditionControls=[
  {form:"quote",text:"승인이 나면 견적 가격은 381만 원으로, 공사 기간은 7일로 바꿔 주세요.",values:{amount:3810000,days:7}},
  {form:"quote",text:"계약이 확정되면, 견적 가격은 381만 원으로 확정하고 공사 기간은 7일로 정해 주세요.",values:{amount:3810000,days:7}},
  {form:"quote",text:"승인이 나면 견적 금액은 삼백사십만 원으로, 공사 일수는 닷새로 정해 주세요.",values:{amount:3400000,days:5}},
  {form:"extra",text:"승인이 나면 추가 공사 금액은 45만 원으로, 부가세는 별도로 확정해 주세요.",values:{amount:450000,vat:"excluded"}},
  {form:"dates",text:"일정이 확정되면, 작업 인원은 4명으로, 작업일은 1월 14일과 21일로 정해 주세요.",values:{crew:4,days:["2027-01-14","2027-01-21"]}},
  {form:"period",text:"승인이 나면 시작일은 1월 8일, 종료일은 2월 3일로 확정해 주세요.",values:{start:"2027-01-08",end:"2027-02-03"}},
] as const;
for(const c of conditionControls)test("공통 조건은 연결된 모든 대입에 적용돼요: "+c.text,async()=>{
  const f=fixture(c.form),values=c.values as AssistCurrent;
  const keys=Object.keys(values);
  for(const key of keys)assert.equal(ruleFields(c.form,c.text,opts).find(x=>x.field===key)?.action,"keep",key);
  await f.send(c.text,r=>keys.map(key=>set(r,key,values[key])));
  for(const key of keys)assert.notEqual(f.field(key).status,"valid",key);
  f.apply({});
});

for(const text of [
  "승인이 나면 유효기간을 수정할까요? 견적 가격은 381만 원으로 확정해 주세요.",
  "유효기간을 수정할 수 있는지 궁금합니다. 견적 가격은 381만 원으로 확정해 주세요.",
  "현재 화면의 견적 가격은 381만 원으로 확정해 주세요.",
]){
  test("독립 문의의 끝과 일반 명사 화면을 조건으로 이어 붙이지 않아요: "+text,async()=>{
    const f=fixture("quote");
    assert.equal(ruleFields("quote",text,opts).find(x=>x.field==="amount")?.value,3810000);
    await f.send(text,r=>[set(r,"amount",3810000,"381만 원")]);
    f.apply({amount:3810000});
  });
}

const linkedKeepCases=[
  {form:"quote",field:"days",label:"작업 기간",next:"견적 가격은 381만 원으로 확정해 주세요.",other:"amount",value:3810000},
  {form:"extra",field:"title",label:"항목명",next:"추가 금액은 37만 원으로 확정해 주세요.",other:"amount",value:370000},
  {form:"dates",field:"days",label:"작업일",next:"작업 인원은 4명으로 확정해 주세요.",other:"crew",value:4},
  {form:"period",field:"end",label:"방문 종료일",next:"방문 제외 날짜는 1월 10일과 19일로 확정해 주세요.",other:"excluded",value:["2027-01-10","2027-01-19"]},
] as const;
for(const c of linkedKeepCases){
  for(const mode of ["whole","scoped","rule","offline","foreign-proof","stale-proof","competing","condition"] as const){
    test("연결된 현재값 유지의 근거와 필드별 보류 "+c.form+" "+mode,async()=>{
      const f=fixture(c.form);
      const seed=c.label+"은 아직 미정입니다.";
      await f.send(seed,r=>[hold(r,c.field)]);
      assert.ok(f.holds[c.field]);
      const scope=c.label+"은 현재 값 그대로 두고";
      const text=(mode==="condition"?"승인이 나면 ":"")+scope+" "+c.next+
        (mode==="competing"?" "+c.label+"은 다시 미정입니다.":"");
      await f.send(text,r=>{
        if(mode==="rule")return ruleResponse(c.form,r.request_id,r.text,opts,r.rule_fields).fields.map(x=>({...x,origin_request_id:r.request_id}));
        const proof=keep(r,c.field,mode==="foreign-proof"?c.next:mode==="scoped"?scope:text);
        if(mode==="stale-proof")proof.origin_request_id="00000000-0000-4000-8000-000000000178";
        return [proof,set(r,c.other,c.value as AssistValue,c.next)];
      },mode==="offline");
      const released=["whole","scoped","rule","offline"].includes(mode);
      assert.equal(Boolean(f.holds[c.field]),!released);
      f.apply(mode==="condition"?{}:{[c.other]:c.value as AssistValue});
      assert.equal(Boolean(f.holds[c.field]),!released);
      assert.deepEqual(f.current[c.field],currents[c.form][c.field]);
    });
  }
}

// Worker22의 별도 합성 대조에서 확인한 문자열 규칙 경계.
const textBoundaryControls=[
  {text:"부가세는 아직 고민 중이고 제목은 문틀 보강으로 지정합니다.",field:"title",value:"문틀 보강",other:"vat"},
  {text:"제목은 아직 모르겠고 사유는 습기 유입으로 지정합니다.",field:"reason",value:"습기 유입",other:"title"},
  {text:"항목명은 보류하고 사유는 현장 확인으로 입력합니다.",field:"reason",value:"현장 확인",other:"title"},
] as const;
for(const c of textBoundaryControls)test("미정인 독립 칸과 명확한 문자열 대입을 분리해요: "+c.text,async()=>{
  const f=fixture("extra");
  assert.equal(ruleFields("extra",c.text,opts).find(x=>x.field===c.field)?.value,c.value);
  await f.send(c.text,()=>[],true);
  f.apply({[c.field]:c.value});
  assert.ok(f.holds[c.other]);
});
for(const [text,field,value] of [
  ["부가세가 포함되는 경우 제목은 문틀 보강으로 지정합니다.","title","문틀 보강"],
  ["제목이 정해지면 사유는 습기 유입으로 지정합니다.","reason","습기 유입"],
  ["가능하면 항목명은 보류하고 사유는 현장 확인으로 입력합니다.","reason","현장 확인"],
  ["사유는 현장 확인으로 입력합니다. 사유는 아직 고민 중입니다.","reason","현장 확인"],
] as const)test("문자열 절의 조건과 동일 칸 경쟁은 유지해요: "+text,async()=>{
  const f=fixture("extra");
  assert.equal(ruleFields("extra",text,opts).find(x=>x.field===field)?.action,"keep");
  await f.send(text,r=>[set(r,field,value)]);
  f.apply({});
});
