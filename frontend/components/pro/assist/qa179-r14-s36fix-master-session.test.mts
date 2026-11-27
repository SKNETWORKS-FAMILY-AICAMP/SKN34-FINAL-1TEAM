import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistForm, type AssistRequest, type AssistCurrent, type CandidateField, type RuleOptions } from "./protocol.ts";

// Exposed product regression, not an independent model evaluation.
const opts: RuleOptions = { today:"2026-11-25", baseMonth:"2026-12-01", requiredDays:2, daysMax:120, crewMax:6, vatModes:["included","excluded","exempt"] };
type Offer = Partial<CandidateField> & {field:string};
function harness(form:AssistForm, initial:AssistCurrent) {
  const current=structuredClone(initial), holds:Record<string,string>={};
  let offers:Offer[]=[];
  let sent:AssistRequest|undefined;
  const session=new AssistSession(form,"developer-r14-regression",async r=>{
    sent=r;
    return {form,source:"llm",request_id:r.request_id,warnings:[],fields:formKeys(form).map(field=>{
      const offer=offers.find(f=>f.field===field);
      return offer
        ? {field,action:"set",value:null,evidence:r.text,source:"llm",status:"valid",alternatives:[],origin_request_id:r.request_id,intent_kind:"explicit_change",value_source:"utterance",...offer}
        : {field,action:"keep",value:null,evidence:"",source:"rule",status:"valid",alternatives:[],origin_request_id:r.request_id,intent_kind:"no_mention",value_source:null};
    })};
  });
  return {session,current,holds,async send(text:string,fields:Offer[]){
    offers=fields;
    assert(session.send(text,current,opts,issues=>{
      for(const [key,msg] of Object.entries(issues.fieldHolds??{})){
        if(msg===null)delete holds[key];else if(typeof msg==="string")holds[key]=msg;
      }
    },()=>({current,manualVersions:{}})));
    assert(!session.apply(current,()=>assert.fail("cannot apply while pending")));
    await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(session.view.phase,"ready");
    assert(sent);
    return sent;
  },field(name:string){return session.view.response?.fields.find(f=>f.field===name)},apply(expected:AssistCurrent){
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,changes=>{patch=structuredClone(changes);Object.assign(current,changes)}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);
    assert(!session.apply(current,()=>assert.fail("duplicate apply")));
  }};
}
const keep=(field:string,evidence:string):Offer=>({field,action:"keep",value:null,evidence,intent_kind:"retain_current",value_source:"current"});
test("withdraw verified pending quantity and keep actual value",async()=>{
  const h=harness("quote",{amount:4550000,vat:"included",days:3,valid:"2026-12-17"});
  try {
    await h.send("작업 소요일은 9일로 수정해 주세요.",[{field:"days",value:9,evidence:"작업 소요일은 9일로 수정"}]);
    assert(h.holds.days);
    const r=await h.send("아까 말씀드린 9일은 취소할게요. 작업 소요일은 원래대로 두세요.",[keep("days","작업 소요일은 원래대로 두세요.")]);
    assert.equal(r.previous_candidate?.fields.find(f=>f.field==="days")?.value,9);
    assert.deepEqual(h.holds,{});
    h.apply({});
    assert.equal(h.current.days,3);
  }finally{h.session.dispose()}
});
test("linked current retention settles only its own obligation",async()=>{
  const h=harness("dates",{days:["2026-12-09","2026-12-16"],crew:2});
  try{
    await h.send("투입 인원은 지금 등록된 대로 하고 방문 날짜만 12월 11일, 12월 18일로 변경해 주세요.",[
      keep("crew","투입 인원은 지금 등록된 대로 하고"),{field:"days",value:["2026-12-11","2026-12-18"],evidence:"방문 날짜만 12월 11일, 12월 18일로 변경"}
    ]);
    assert.deepEqual(Object.keys(h.holds),["days"]);
    h.apply({days:["2026-12-11","2026-12-18"]});
    assert.deepEqual(h.holds,{});
    assert.equal(h.current.crew,2);
  }finally{h.session.dispose()}
});
test("dated recommendation with explicit preview is not an open condition",async()=>{
  const h=harness("period",{start:"2026-12-02",end:"2026-12-24",excluded:[]});
  try{
    await h.send("현장 준비를 생각하면 12월 5일에 시작하는 걸로 변경하는 게 좋겠어요. 변경안 보여 주세요.",[{field:"start",value:"2026-12-05",evidence:"12월 5일에 시작하는 걸로 변경"}]);
    assert.equal(h.field("start")?.status,"valid");
    assert.equal(h.field("start")?.value,"2026-12-05");
    assert.equal(h.current.start,"2026-12-02");
    assert(h.holds.start);
  }finally{h.session.dispose()}
});
test("conditional recommendation remains held",async()=>{
  const h=harness("period",{start:"2026-12-02",end:"2026-12-24",excluded:[]});
  try{
    await h.send("자재가 도착한다면 12월 5일에 시작하는 걸로 변경하는 게 좋겠어요. 변경안 보여 주세요.",[{field:"start",value:"2026-12-05",evidence:"12월 5일에 시작하는 걸로 변경"}]);
    assert.notEqual(h.field("start")?.status,"valid");
    h.apply({});
  }finally{h.session.dispose()}
});
test("an exact reason proof must not authorize the other text field",async()=>{
  const h=harness("extra",{title:"마루 정리",reason:"현장 협의",amount:780000,vat:"included"});
  const text="추가 작업을 하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오.";
  try{
    await h.send(text,[{field:"title",value:"하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오",source:"rule"},{field:"reason",value:"매립 배선 위치 변경",evidence:"경위란에 '매립 배선 위치 변경'이라고 남겨"}]);
    assert.equal(h.field("reason")?.status,"valid");
    assert.notEqual(h.field("title")?.status,"valid");
    h.apply({reason:"매립 배선 위치 변경"});
    assert.deepEqual(h.holds,{});
  }finally{h.session.dispose()}
});
for(const value of [3,8])test("relative quantity validates derived result rather than operand: "+value,async()=>{
  const h=harness("quote",{amount:4550000,vat:"included",days:5,valid:"2026-12-17"});
  try{
    const text="기존 작업 일수에서 사흘 더 늘려 주시겠어요?";
    await h.send(text,[{field:"days",value,value_source:"derived",evidence:"기존 작업 일수에서 사흘 더 늘려 주시겠어요?"}]);
    if(value===8){assert.equal(h.field("days")?.status,"valid");h.apply({days:8})}
    else{assert.notEqual(h.field("days")?.status,"valid");h.apply({});assert(h.holds.days)}
  }finally{h.session.dispose()}
});
test("an unrequested competing text offer cannot become a selectable conflict",async()=>{
  const h=harness("extra",{title:"마루 정리",reason:"현장 협의",amount:780000,vat:"included"});
  const text="현장 치수가 달라서 추가 공사비를 76만원으로 바꿔 주세요. 자재는 확인했습니다.";
  try{
    await h.send(text,[{field:"reason",action:"uncertain",status:"conflict",value:null,evidence:text,alternatives:[
      {action:"set",value:"현장 치수가 달라서",evidence:"현장 치수가 달라서",source:"rule"},
      {action:"set",value:"자재는 확인했습니다",evidence:"자재는 확인했습니다",source:"llm"}
    ]},{field:"amount",value:760000,evidence:"추가 공사비를 76만원으로 바꿔 주세요"}]);
    assert.notEqual(h.field("reason")?.status,"conflict");
    h.apply({amount:760000});
    assert(!h.holds.reason);
  }finally{h.session.dispose()}
});
