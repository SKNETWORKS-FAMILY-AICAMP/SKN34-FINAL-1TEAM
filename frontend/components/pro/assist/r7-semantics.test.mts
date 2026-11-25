// R7: 동결한 실제 응답 재생 및 독립 대조.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type AssistValue, type CandidateField, type RuleOptions } from "./protocol.ts";
import { requestIntent } from "./requestIntent.ts";
import { ruleFields } from "./rules.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2027-01-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const quote:AssistCurrent={amount:3260000,vat:"included",days:4,valid:"2027-01-27"};
const period:AssistCurrent={start:"2027-01-06",end:"2027-01-29",excluded:["2027-01-14","2027-01-22"]};
const extra:AssistCurrent={title:"기존 보강",reason:"기존 손상",amount:320000,vat:"included"};
const resource="00000000-0000-4000-8000-000000000179";
type PartialField=Partial<CandidateField>;
function set(r:AssistRequest,field:string,value:AssistValue,evidence=r.text,source:"llm"|"rule"="llm"):PartialField{
  return {field,action:"set",status:"valid",value,evidence,source,intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,alternatives:[]};
}
function hold(r:AssistRequest,field:string,evidence:string,status:"uncertain"|"invalid"="uncertain",reason:"ambiguous_intent"|"evidence_missing"|"unsupported_normalization"="ambiguous_intent"):PartialField{
  return {field,action:"uncertain",status,value:null,evidence,source:"llm",intent_kind:"ambiguous",origin_request_id:r.request_id,reason_code:reason,alternatives:[]};
}
function carry(r:AssistRequest,field:string):PartialField{
  const p=r.previous_candidate, old=p?.fields.find(f=>f.field===field);
  assert(p&&old,field);
  return {...old,status:"valid",alternatives:[],source:p.sources![field],origin_request_id:p.origins![field],intent_kind:"retain_previous",value_source:"previous"};
}
function fixture(form:AssistForm,initial:AssistCurrent){
  let current=structuredClone(initial),pending=false,request:AssistRequest;
  const versions:Record<string,number>={},holds:Record<string,string>={};
  let reply:(r:AssistRequest)=>PartialField[];
  let warnings:string[]=[];
  let fail=false;
  const session=new AssistSession(form,resource,async r=>{
    request=structuredClone(r);
    if(fail)throw new Error("offline");
    const fields=reply(r);
    return {form,request_id:r.request_id,source:"llm",warnings,
      fields:formKeys(form).map(field=>({field,action:"keep",status:"valid",value:null,evidence:"",source:"rule",
        intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id,alternatives:[],correction:null,reason_code:null,
        ...fields.find(f=>f.field===field)}))};
  });
  async function send(text:string,respond:typeof reply,offline=false,notes:string[]=[]){
    reply=respond;warnings=notes;fail=offline;
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
    assert(pending);
    assert.equal(session.apply(current,()=>assert.fail("응답 전 적용")),false);
    await ready;off();
    assert(!pending);
    return request!;
  }
  function field(key:string){return session.view.response!.fields.find(f=>f.field===key)!;}
  function manual(key:string,value:AssistValue){current[key]=value;versions[key]=(versions[key]??0)+1;delete holds[key];}
  function apply(expected:AssistCurrent){
    const selected=Object.keys(expected);
    for(const key of formKeys(form))session.select(key,selected.includes(key));
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,v=>{patch=structuredClone(v);Object.assign(current,v);}),selected.length>0);
    assert.deepEqual(patch,expected);
  }
  return {session,send,field,manual,apply,holds,get current(){return current;}};
}

// 실제 호출 FUT14-quote-07의 T1, T2 값/근거를 보존해 재생해요.
const seed07="견적 금액은 298만 4천원, 공사 기간은 9일을 제안해 주세요.";
const tax07="세금 방식도 면세로 선택해 주세요.";
const group07="앞에 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요.";
async function seedQuote(f:ReturnType<typeof fixture>){
  const first=await f.send(seed07,r=>[set(r,"amount",2984000,"298만 4천원"),set(r,"days",9,"공사 기간은 9일")]);
  const second=await f.send(tax07,r=>[carry(r,"amount"),carry(r,"days"),set(r,"vat","exempt","세금 방식도 면세로 선택해 주세요")]);
  return {first,second};
}
test("저장된 quote07 거절 응답은 브라우저가 되살리지 않아요",async()=>{
  const f=fixture("quote",quote);await seedQuote(f);
  await f.send(group07,r=>[hold(r,"amount","","invalid","evidence_missing"),hold(r,"days","","invalid","evidence_missing"),carry(r,"vat")],false,["원문 근거나 업무 조건을 확인할 수 없는 후보가 있어요."]);
  f.apply({vat:"exempt"});assert.ok(f.holds.amount&&f.holds.days);
});
test("수정 서버 quote07 공동 원문 근거와 필드별 이력 출처를 보존해요",async()=>{
  const f=fixture("quote",quote);const {first,second}=await seedQuote(f);
  const intent=requestIntent("quote",group07);
  for(const key of ["amount","days","vat"]){assert.equal(intent.priorReferences.get(key),"previous");assert(!intent.blocked.has(key));}
  const third=await f.send(group07,r=>[carry(r,"amount"),carry(r,"days"),carry(r,"vat")]);
  assert.equal(third.previous_candidate?.origins?.amount,first.request_id);
  assert.equal(f.field("amount").origin_request_id,first.request_id);
  assert.equal(f.field("days").origin_request_id,first.request_id);
  assert.equal(f.field("vat").origin_request_id,second.request_id);
  f.apply({amount:2984000,days:9,vat:"exempt"});assert.deepEqual(f.holds,{});
});
for(const text of [
  "앞서 제안했던 공사 기간 및 세금 구분, 견적액을 그대로 반영해 주세요.",
  "직전에 정한 금액, 공기와 부가세 방식을 그대로 사용합니다.",
  "이전에 입력한 총액·일수·세금 조건을 그대로 적용합니다.",
]){
  test("공통 과거 수식어가 목록 전체를 소유해요: "+text,async()=>{
    const f=fixture("quote",quote);await seedQuote(f);
    const intent=requestIntent("quote",text);
    for(const key of ["amount","days","vat"])assert.equal(intent.priorReferences.get(key),"previous");
    await f.send(text,r=>["amount","days","vat"].map(key=>carry(r,key)));
    f.apply({amount:2984000,days:9,vat:"exempt"});
  });
}
for(const mode of ["fresh","wrong-origin","wrong-source","wrong-value","wrong-evidence","missing","manual","first","unowned","cancel","competing","condition","question","incomplete"] as const){
  test("공동 유지의 필드별 보류: "+mode,async()=>{
    const f=fixture("quote",quote);const {first}=await seedQuote(f);
    if(mode==="manual")f.manual("amount",quote.amount);
    if(mode==="missing")await f.send("부가세 조건을 면세로 확정해 주세요.",r=>[set(r,"vat","exempt")]);
    if(mode==="first")await f.send("견적 금액은 310만원으로 정해 주세요.",r=>[set(r,"amount",3100000),carry(r,"days"),carry(r,"vat")]);
    let text=group07;
    if(mode==="first")text="처음 제시한 총액과 공사 일수, 세금 조건을 그대로 적용해 주세요.";
    if(mode==="unowned")text="앞에 제시한 공사 일수와 세금 조건을 그대로 적용해 주세요. 금액은 아직 미정입니다.";
    if(mode==="cancel")text+=" 금액 변경은 취소합니다.";
    if(mode==="competing")text+=" 견적 금액은 330만원으로 변경합니다.";
    if(mode==="condition")text="가능하면 "+text;
    if(mode==="question")text=text.replace("적용해 주세요.","적용할까요?");
    if(mode==="incomplete")text="앞에 제시한 총액과 공사 일수, 세금 조건";
    const r2=await f.send(text,r=>{
      const bad=r.previous_candidate?.fields.some(x=>x.field==="amount")?carry(r,"amount"):
        {...set(r,"amount",2984000,"298만 4천원"),intent_kind:"retain_previous" as const,value_source:"previous" as const,origin_request_id:first.request_id};
      if(mode==="fresh")Object.assign(bad,set(r,"amount",2984000,"총액을 그대로 적용해 주세요"));
      if(mode==="wrong-origin")bad.origin_request_id=r.request_id;
      if(mode==="wrong-source")bad.source="rule";
      if(mode==="wrong-value")bad.value=3300000;
      if(mode==="wrong-evidence")bad.evidence="총액을 그대로 적용해 주세요";
      const rest=["days","vat"].filter(k=>r.previous_candidate?.fields.some(x=>x.field===k)).map(k=>carry(r,k));
      return [bad,...rest];
    });
    assert.notEqual(f.field("amount").status,"valid");
    if(mode==="condition"||mode==="question"||mode==="incomplete"){
      assert.notEqual(f.field("vat").status,"valid");f.apply({});
    } else if(mode==="missing")f.apply({vat:"exempt"});
    else if(mode==="first")f.apply({days:9,vat:"exempt"}); // VAT was first proposed on T2, still its first origin.
    else f.apply({days:9,vat:"exempt"});
    assert.ok(r2.request_id);
  });
}

// 같은 구조의 다른 폼. 서버가 원문의 명칭을 새 문자열 값으로 채우지 않아요.
for(const sample of [
  {form:"extra" as const,current:extra,seed:"항목명은 벽체 보강, 사유는 접착 불량으로 정해 주세요.",values:{title:"벽체 보강",reason:"접착 불량"},text:"앞서 제시한 항목명과 사유를 그대로 반영해 주세요."},
  {form:"dates" as const,current:{days:["2027-01-08","2027-01-12"],crew:2},seed:"작업일은 1월 14일과 21일, 작업 인원은 4명으로 정해 주세요.",values:{days:["2027-01-14","2027-01-21"],crew:4},text:"앞에 제시한 작업 일정과 인원을 그대로 적용해 주세요."},
  {form:"period" as const,current:period,seed:"시작일은 1월 8일, 종료일은 2월 3일로 정해 주세요.",values:{start:"2027-01-08",end:"2027-02-03"},text:"앞서 제시한 시작일과 종료일을 그대로 반영해 주세요."},
]){
  test(sample.form+" 공동 참조의 원래 이력을 적용해요",async()=>{
    const f=fixture(sample.form,sample.current);
    const keys=Object.keys(sample.values);
    await f.send(sample.seed,r=>keys.map(k=>set(r,k,(sample.values as AssistCurrent)[k])));
    await f.send(sample.text,r=>keys.map(k=>carry(r,k)));
    for(const key of keys)assert.equal(requestIntent(sample.form,sample.text).priorReferences.get(key),"previous");
    f.apply(sample.values);
  });
}

// 실제 quote05: 원래 잘못된 최종 서버 응답을 되살리지 않으며 새 정상 응답은 검증해요.
const vat05="부가세는 따로 청구하는 것으로 선택해 주세요.";
test("저장 quote05 거절과 수정 VAT 후보는 구별해요",async()=>{
  for(const corrected of [false,true]){
    const f=fixture("quote",quote);
    await f.send("이번 견적액을 513만 원으로 올려 놓을 후보가 필요합니다.",r=>[set(r,"amount",5130000,"견적액을 513만 원으로")]);
    await f.send(vat05,r=>[corrected?set(r,"vat","excluded","부가세는 따로 청구하는 것으로 선택해 주세요"):hold(r,"vat",vat05.slice(0,-1),"invalid","unsupported_normalization")]);
    f.apply(corrected?{vat:"excluded"}:{});assert.equal(f.current.amount,quote.amount);
  }
});
const vatCases=[
  ["부가세는 따로 청구하는 것으로 선택해 주세요.","excluded"],
  ["세금 방식은 별도로 청구해 주세요.","excluded"],
  ["부가세는 미포함으로 지정해 주세요.","excluded"],
  ["부가세를 포함하는 것으로 정해 주세요.","included"],
  ["세금 조건은 면세로 선택해 주세요.","exempt"],
] as const;
for(const form of ["quote","extra"] as const)for(const [text,mode] of vatCases){
  test(form+" VAT 의미와 서버 값 대조: "+text,async()=>{
    assert.equal(ruleFields(form,text,opts).find(x=>x.field==="vat")?.value,mode);
    for(const wrong of [false,true]){
      const f=fixture(form,form==="quote"?quote:extra);
      await f.send(text,r=>[set(r,"vat",wrong?(mode==="excluded"?"included":"excluded"):mode)]);
      if(wrong)assert.notEqual(f.field("vat").status,"valid");
      else assert.equal(f.field("vat").status,"valid");
      f.apply(wrong?{}:mode==="included"?{}:{vat:mode});
    }
  });
}
for(const text of [
  "부가세는 따로 청구할까요?",
  "가능하면 부가세는 따로 청구해 주세요.",
  "부가세는 따로 청구하지 마세요.",
  "부가세 별도 또는 포함으로 고민 중입니다.",
  "부가세는 따로 청구해 주세요. 세금 방식 변경은 취소합니다.",
  "부가세는 별도 청구로 정해 주세요. 부가세는 포함으로 바꿔 주세요.",
  "부가세는 따로 확인하고 철거비는 별도로 청구합니다.",
  "부가세는 포함이고 운반비는 따로 청구합니다.",
]){
  test("VAT 의미 소유권과 위험: "+text,async()=>{
    const f=fixture("quote",quote);
    await f.send(text,r=>[set(r,"vat","excluded")]);
    assert.notEqual(f.field("vat").status,"valid");f.apply({});
  });
}
test("타 필드 문자 속 VAT를 근거로 사용하지 않아요",async()=>{
  const f=fixture("extra",extra);
  const text='제목은 "부가세 별도 청구 안내"로 적어 주세요. 금액은 47만 원으로 확정합니다.';
  await f.send(text,r=>[set(r,"title","부가세 별도 청구 안내"),set(r,"amount",470000),set(r,"vat","excluded","부가세 별도 청구 안내")]);
  f.apply({title:"부가세 별도 청구 안내",amount:470000});
});
test("서로 다른 VAT 대안도 같은 원문 값으로 축소하지 않아요",async()=>{
  const f=fixture("quote",quote);
  const text=vat05;
  await f.send(text,r=>[{field:"vat",status:"conflict",action:"uncertain",value:null,evidence:text,source:"llm",origin_request_id:r.request_id,alternatives:[{action:"set",value:"excluded",evidence:"따로 청구",source:"rule"},{action:"set",value:"exempt",evidence:text,source:"llm"}]}]);
  assert.notEqual(f.field("vat").status,"valid");
  f.apply({});
});

// 실제 period05: 후보 자체를 요청한 명시적 날짜와 가능 여부 질문을 구별해요.
const deadline05="방문 가능한 마감일을 2027년 2월 2일로 제안해 주세요.";
test("저장 period05 거절은 보류, 수정 규칙 후보는 정상 적용",async()=>{
  for(const corrected of [false,true]){
    const f=fixture("period",period);
    const r=await f.send(deadline05,r=>[corrected?set(r,"end","2027-02-02",deadline05,"rule"):hold(r,"end",deadline05.slice(0,-1))]);
    assert.equal(r.rule_fields.find(x=>x.field==="end")?.value,"2027-02-02");
    f.apply(corrected?{end:"2027-02-02"}:{});
  }
});
for(const [text,expected] of [
  ["실측 종료일은 2월 5일로 제안해 주세요.","2027-02-05"],
  ["마감일은 2월 4일로 확정합니다.","2027-02-04"],
  ["방문 가능한 마감일은 2027년 2월 2일로 지정합니다.","2027-02-02"],
] as const){
  test("명시적 마감일 원문과 서버 값 대조: "+text,async()=>{
    for(const mode of ["correct","wrong","foreign-proof","uncertain","offline"] as const){
      const f=fixture("period",period);
      await f.send(text,r=>[mode==="uncertain"?hold(r,"end",text):set(r,"end",mode==="wrong"?"2027-02-08":expected,mode==="foreign-proof"?"2월 8일":text,"rule")],mode==="offline");
      if(mode==="wrong"||mode==="foreign-proof"||mode==="uncertain")assert.notEqual(f.field("end").status,"valid");
      f.apply(mode==="correct"||mode==="offline"?{end:expected}:{});
    }
  });
}
for(const text of [
  "마감일은 2월 2일로 제안해도 될까요?",
  "마감일은 2월 2일인지 아직 확인 중입니다.",
  "가능하면 마감일은 2월 2일로 제안해 주세요.",
  "마감일은 2월 2일로 지정하지 마세요.",
  "마감일은 2월 2일 또는 2월 4일로 생각 중입니다.",
  "마감일은 2월 2일로 지정합니다. 마감일 변경은 취소합니다.",
  "마감일은 2027년 2월 31일로 제안해 주세요.",
]){
  test("위험하거나 잘못된 마감일 후보 보류: "+text,async()=>{
    const f=fixture("period",period);
    await f.send(text,r=>[set(r,"end","2027-02-02",text,"rule")]);
    assert.notEqual(f.field("end").status,"valid");f.apply({});
  });
}
test("독립 문의 제외일은 보류하면서 정확한 마감일 적용",async()=>{
  const f=fixture("period",period);
  const text="방문 제외일은 1월 18일인지 아직 미정입니다. 마감일은 2월 4일로 제안해 주세요.";
  await f.send(text,r=>[hold(r,"excluded","방문 제외일은 1월 18일인지 아직 미정입니다"),set(r,"end","2027-02-04","마감일은 2월 4일로 제안해 주세요","rule")]);
  f.apply({end:"2027-02-04"});assert.ok(f.holds.excluded);
});

test("가능 기간 종료일의 명칭은 끝날의 단일 소유권이에요",async()=>{
  const text="가능 기간 종료일은 2월 8일로 지정해 주세요.";
  const intent=requestIntent("period",text);
  assert.deepEqual([...intent.requested],["end"]);
  const rule=ruleFields("period",text,opts);
  assert.equal(rule.find(x=>x.field==="start")?.action,"keep");
  assert.equal(rule.find(x=>x.field==="end")?.value,"2027-02-08");
  const f=fixture("period",period);
  await f.send(text,r=>[set(r,"start","2027-02-08",text,"rule"),set(r,"end","2027-02-08",text,"rule")]);
  assert.notEqual(f.field("start").status,"valid");
  f.apply({end:"2027-02-08"});
});
test("마감일 뒤의 무대상 경쟁 후보는 시작일이 아니에요",async()=>{
  const text="종료일은 2월 2일로 변경해 주세요. 4일도 후보입니다.";
  const intent=requestIntent("period",text);
  assert.deepEqual([...intent.requested],["end"]);
  const f=fixture("period",period);
  await f.send(text,r=>[set(r,"end","2027-02-02","종료일은 2월 2일로 변경해 주세요")]);
  assert.notEqual(f.field("end").status,"valid");f.apply({});
});

// v2: v1 동결 뒤 실제 실행된 추가 회귀 결과에서 확인한 의미 조합.
for(const [form,text,evidence,value] of [
  ["quote","공사 기간은 앞서 제시한 값, 세금은 별도 처리로 부탁합니다.","세금은 별도 처리로 부탁합니다","excluded"],
  ["extra","부가세 구분만 별도로 돌려주세요.","부가세 구분만 별도로 돌려주세요","excluded"],
  ["extra","면세로 잡고, 추가된 사유에는 몰딩 하부 균열이라고 적어주세요.","면세로 잡고","exempt"],
  ["extra","아 이 건은 면세로 처리할게요.","면세로 처리할게요","exempt"],
  ["extra","면세로 정정하고 사유는 기존 내용 그대로 남겨 주세요.","면세로 정정하고","exempt"],
  ["quote","작업 기간 8일로 잡고요, 부가세는 포함 말고 별도라고 표시해 주세요.","부가세는 포함 말고 별도","excluded"],
  ["extra","추가 항목은 문틀 재시공, 사유는 기존 문틀 변형 발견, 금액 47만 원, 부가세 별도로 올려 주세요.","부가세 별도로","excluded"],
  ["extra","부가세는 별도 확정이고 나머지는 그대로 유지해주세요","부가세는 별도 확정","excluded"],
  ["extra","부가세는 별도로 정정하고 나머지는 앞서 제안한 그대로 유지","부가세는 별도로 정정","excluded"],
  ["quote","일단 견적액 518만원이랑 세금 별도 안으로 보여주세요","세금 별도","excluded"],
  ["extra","부가세는 별도로 다시 후보에 넣어주세요","부가세는 별도로 다시 후보에 넣어주세요","excluded"],
  ["quote","견적 총액 537만원과 별도 세금으로 먼저 제시해주세요","별도 세금","excluded"],
  ["quote","부가세 별도 방식으로 기재 부탁","부가세 별도 방식","excluded"],
  ["quote","부가세 별도로 최종 정해주세요","부가세 별도로 최종 정해주세요","excluded"],
  ["extra","부가세 별도 방식으로 표기해주세요","부가세 별도 방식","excluded"],
  ["quote","세금 조건을 미포함 처리로 부탁드립니다.","미포함 처리로 부탁드립니다","excluded"],
] as const){
  test("v2 VAT 값/완결 서술어/클립 근거: "+text,async()=>{
    const rule=ruleFields(form,text,opts);
    assert.equal(rule.find(x=>x.field==="vat")?.value,value);
    for(const wrong of [false,true]){
      const f=fixture(form,form==="quote"?quote:extra);
      await f.send(text,r=>[set(r,"vat",wrong?"included":value,evidence)]);
      assert.equal(f.field("vat").status==="valid",!wrong);
      f.apply(wrong?{}:{vat:value});
    }
  });
}
test("v2 현재 상태 보고와 독립 새 VAT 변경을 구분해요",async()=>{
  const text="지금 부가세 포함으로 돼 있는데, 그거만 부가세 별도로 바꿔 주세요.";
  assert.equal(ruleFields("quote",text,opts).find(x=>x.field==="vat")?.value,"excluded");
  const f=fixture("quote",quote);
  await f.send(text,r=>[set(r,"vat","excluded","부가세 별도로 바꿔 주세요")]);
  f.apply({vat:"excluded"});
});
for(const text of [
  "세금은 별도 처리로 부탁드릴까요?",
  "부가세를 별도로 돌리지 마세요.",
  "부가세를 별도로 확인하고 금액은 40만 원으로 올려 주세요.",
  "면세점에서 작업명은 확인하고 금액 45만 원으로 입력해 주세요.",
  "사유는 별도 처리로 부탁합니다.",
  "부가세 포함으로 돼 있는데 별도 비용도 확인해 주세요.",
  "지금 부가세 포함으로 돼 있는데, 부가세 별도로 바꾸면 될까요?",
  "부가세는 별도 확정이고 다시 포함으로 확정해 주세요.",
  "부가세는 별도라고 표기하지 마세요.",
]){
  test("v2 VAT 일반 명사/질문/부정/경쟁 보류: "+text,async()=>{
    const f=fixture("extra",extra);
    await f.send(text,r=>[set(r,"vat","excluded",text)]);
    assert.notEqual(f.field("vat").status,"valid");f.apply({});
  });
}
test("v2 독립된 시작일 확정은 종료일 문의와 분리해요",async()=>{
  const text="시작일은 12월 5일로 변경해 주세요. 종료일은 12월 28일로 바꾸면 될까요?";
  const rule=ruleFields("period",text,opts);
  assert.equal(rule.find(x=>x.field==="start")?.value,"2026-12-05");
  assert.equal(rule.find(x=>x.field==="end")?.action,"keep");
  const initial={start:"2026-12-02",end:"2026-12-24",excluded:[]};
  const f=fixture("period",initial);
  await f.send(text,r=>[set(r,"start","2026-12-05","시작일은 12월 5일로 변경해 주세요"),hold(r,"end","종료일은 12월 28일")]);
  f.apply({start:"2026-12-05"});assert.ok(f.holds.end);
});
test("v2 다른 경계 문의가 선행해도 명시된 종료일만 적용해요",async()=>{
  const text="시작일은 2월 5일로 바꾸면 될까요? 종료일은 2월 28일로 변경해 주세요.";
  const rule=ruleFields("period",text,opts);
  assert.equal(rule.find(x=>x.field==="start")?.action,"keep");
  assert.equal(rule.find(x=>x.field==="end")?.value,"2027-02-28");
  const f=fixture("period",period);
  await f.send(text,r=>[hold(r,"start","시작일은 2월 5일"),set(r,"end","2027-02-28","종료일은 2월 28일로 변경해 주세요")]);
  f.apply({end:"2027-02-28"});assert.ok(f.holds.start);
});

// v3: 명시한 세금 방식으로 후보를 보여 달라는 완결 요청만 읽어요.
for(const [form,text,evidence] of [
  ["extra","추가금 58만 4천 원, 부가세 별도로 먼저 후보를 보여주세요.","부가세 별도로"],
  ["quote","세금은 별도로 초안을 보여 주세요.","세금은 별도로 초안을 보여 주세요"],
  ["extra","부가세는 별도로 제안안을 보여 주세요.","별도로 제안안을 보여 주세요"],
] as const){
  test("v3 VAT 후보 목적어와 완결 제시 요청: "+text,async()=>{
    assert.equal(ruleFields(form,text,opts).find(x=>x.field==="vat")?.value,"excluded");
    for(const wrong of [false,true]){
      const f=fixture(form,form==="quote"?quote:extra);
      await f.send(text,r=>[set(r,"vat",wrong?"included":"excluded",evidence)]);
      assert.equal(f.field("vat").status==="valid",!wrong);
      f.apply(wrong?{}:{vat:"excluded"});
    }
  });
}
for(const text of [
  "부가세 별도로 후보를 검토해 주세요.",
  "부가세 별도로 후보를 보여주지 마세요.",
  "가능하면 부가세 별도로 후보를 보여 주세요.",
  "부가세 별도로 후보를 보여주면 될까요?",
  "사유는 별도로 후보를 보여 주세요.",
  "부가세는 별도로 비용을 보여 주세요.",
  "부가세 별도로 후보를 보여 주세요. 부가세 포함으로 확정해 주세요.",
]){
  test("v3 VAT 후보 목적어의 다른 행위/위험 보류: "+text,async()=>{
    const f=fixture("extra",extra);
    await f.send(text,r=>[set(r,"vat","excluded",text)]);
    assert.notEqual(f.field("vat").status,"valid");f.apply({});
  });
}
