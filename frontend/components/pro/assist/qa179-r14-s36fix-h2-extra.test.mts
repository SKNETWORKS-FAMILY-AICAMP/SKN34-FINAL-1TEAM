import assert from "node:assert/strict";
import test from "node:test";
import {extraTextGrounding,extraTextScopes,parseExtra,positiveExtraTextValue} from "./parse.ts";
import {requestIntent} from "./requestIntent.ts";
import {AssistSession} from "./session.ts";
import {formKeys,type AssistCurrent,type CandidateField,type AssistRequest,type RuleOptions} from "./protocol.ts";

const today="2026-11-26";
for (const c of [
  {field:"title" as const,text:'앞서 제안한 항목명 "문틀 보강" 후보를 철회합니다.',valueText:'"문틀 보강"'},
  {field:"reason" as const,text:"앞서 제안한 사유 '현장 높이 차이' 후보를 철회합니다.",valueText:"'현장 높이 차이'"},
]) test("selected historical quote remains available as withdrawal proof: "+c.field,()=>{
  const intent=requestIntent("extra",extraTextScopes(c.text).intentText);
  assert.equal(intent.withdrawalProofs.get(c.field)?.valueText,c.valueText);
  assert.equal(parseExtra(c.text,today)[c.field],undefined);
});
const opts:RuleOptions={today,baseMonth:"2026-12-01",crewMax:6,daysMax:120,vatModes:["included","excluded","exempt"]};
type TextField="title"|"reason";
type Offer=Partial<CandidateField>&{field:string};
const positiveCases: {text:string;field:TextField;value:string;evidence:string}[]=[
  {text:"추가 공사명은 창틀 실리콘 재시공, 추가 사유는 창틀 누수 확인으로 각각 수정해 주세요.",field:"title",value:"창틀 실리콘 재시공",evidence:"추가 공사명은 창틀 실리콘 재시공"},
  {text:"추가 공사명은 창틀 실리콘 재시공, 추가 사유는 창틀 누수 확인으로 각각 수정해 주세요.",field:"reason",value:"창틀 누수 확인",evidence:"추가 사유는 창틀 누수 확인"},
  {text:"부가세는 처음 상태로 돌려놓고 공사명만 거실 조명 이설로 수정해 주세요.",field:"title",value:"거실 조명 이설",evidence:"공사명만 거실 조명 이설로 수정"},
  {text:"공사명을 베란다 배수구 보수로 바꾸겠습니다.",field:"title",value:"베란다 배수구 보수",evidence:"공사명을 베란다 배수구 보수로 바꾸겠습니다"},
  {text:"현장 보고서에 맞춰 추가 사유는 '바닥 누수 확인'으로 수정해 주세요.",field:"reason",value:"바닥 누수 확인",evidence:"추가 사유는 '바닥 누수 확인'으로 수정"},
  {text:"추가한 이유를 '천장 내부 결로 확인'으로 기록해 주세요.",field:"reason",value:"천장 내부 결로 확인",evidence:"이유를 '천장 내부 결로 확인'으로 기록"},
  {text:"부품 수급이 늦지만 사유는 '규격 불일치'로 수정하고 추가 금액은 72만원으로 확정합니다.",field:"reason",value:"규격 불일치",evidence:"사유는 '규격 불일치'로 수정"},
  {text:"사유 '도면 누락'으로 수정안을 먼저 보여 주세요.",field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'으로 수정안"},
  {text:"사유 '현장 높이 차이'로 변경안을 확인합니다.",field:"reason",value:"현장 높이 차이",evidence:"사유 '현장 높이 차이'로 변경안"},
  {text:"추가 작업을 하게 된 경위란에 '매립 배선 위치 변경'이라고 남겨 주십시오.",field:"reason",value:"매립 배선 위치 변경",evidence:"경위란에 '매립 배선 위치 변경'이라고 남겨"},
  {text:"공사명은 타일 줄눈 보완으로, 추가 사유는 자재 규격 차이로 각기 기록해 주십시오.",field:"title",value:"타일 줄눈 보완",evidence:"공사명은 타일 줄눈 보완으로"},
  {text:"공사명은 타일 줄눈 보완으로, 추가 사유는 자재 규격 차이로 각기 기록해 주십시오.",field:"reason",value:"자재 규격 차이",evidence:"추가 사유는 자재 규격 차이로"},
  {text:"확인 기록에 맞추어 작업명은 배관 연결 보완으로 고치겠습니다.",field:"title",value:"배관 연결 보완",evidence:"작업명은 배관 연결 보완으로 고치겠습니다"},
  {text:"작업명 “현관 문틀 보완”으로 변경안을 우선 보여 주십시오.",field:"title",value:"현관 문틀 보완",evidence:"작업명 “현관 문틀 보완”으로 변경안"},
];
for(const c of positiveCases)test("complete owned extra text proof: "+c.text+" ["+c.field+"]",()=>{
  const intent=requestIntent("extra",extraTextScopes(c.text).intentText);
  assert.equal(positiveExtraTextValue(c.text,c.field,c.value,c.evidence,today),true,JSON.stringify({grounding:extraTextGrounding(c.text,c.field),local:parseExtra(c.text,today),requested:[...intent.requested],blocked:[...intent.blocked]}));
  assert.equal(parseExtra(c.text,today)[c.field],c.value);
  const other=c.field==="reason"?"title":"reason";
  assert.equal(positiveExtraTextValue(c.text,other,c.value,c.evidence,today),false);
  if(c.value.includes(" "))assert.equal(positiveExtraTextValue(c.text,c.field,c.value.split(" ").slice(1).join(" "),c.evidence,today),false);
});

const denied:{text:string;field:TextField;value:string;evidence:string}[]=[
  {text:"자재가 도착하면 사유는 '규격 불일치'로 수정해 주세요.",field:"reason",value:"규격 불일치",evidence:"사유는 '규격 불일치'로 수정"},
  {text:"사유 '도면 누락'으로 수정안을 먼저 보여 줄 수 있나요?",field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'으로 수정안"},
  {text:"사유 '도면 누락'으로 수정안을 검토 중입니다.",field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'으로 수정안"},
  {text:"사유 '도면 누락'으로 수정안",field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'으로 수정안"},
  {text:"사유는 '도면 누락'으로 각각 수정하지 마세요.",field:"reason",value:"도면 누락",evidence:"사유는 '도면 누락'으로 각각 수정"},
  {text:"현장 보고서에 맞춰 금액만 80만원으로 수정해 주세요.",field:"reason",value:"현장 보고서에 맞춰",evidence:"현장 보고서에 맞춰"},
  {text:"부품 수급이 늦지만 추가 금액은 72만원으로 확정합니다.",field:"reason",value:"부품 수급이 늦지만",evidence:"부품 수급이 늦지만"},
  {text:"공사명을 베란다 배수구 보수로 바꾸려고 합니다.",field:"title",value:"베란다 배수구 보수",evidence:"공사명을 베란다 배수구 보수로 바꾸려고"},
  {text:"공사명은 문턱 보강, 사유는 '균열 확인'으로 수정안을 검토 중입니다.",field:"reason",value:"균열 확인",evidence:"사유는 '균열 확인'으로 수정안"},
  {text:"작업명 “현관 문틀 보완”으로 변경안을 보여 주지 마세요.",field:"title",value:"현관 문틀 보완",evidence:"작업명 “현관 문틀 보완”으로 변경안"},
  {text:"사유 '도면 누락'",field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'"},
  {text:"작업명 “현관 문틀 보완”으로 변경안",field:"title",value:"현관 문틀 보완",evidence:"작업명 “현관 문틀 보완”으로 변경안"},
  {text:"보고서 승인이 나면 사유는 '도면 누락'으로 수정해 주세요.",field:"reason",value:"도면 누락",evidence:"사유는 '도면 누락'으로 수정"},
  {text:"아까 말씀드린 이유를 '도면 누락'으로 수정해 주세요.",field:"reason",value:"도면 누락",evidence:"이유를 '도면 누락'으로 수정"},
];
for(const c of denied)test("no positive proof for incomplete, descriptive or non-change: "+c.text,()=>{
  assert.equal(positiveExtraTextValue(c.text,c.field,c.value,c.evidence,today),false);
});

function harness() {
  const current:AssistCurrent={title:"마루 정리",reason:"현장 협의",amount:780000,vat:"included"};
  let offers:Offer[]|((r:AssistRequest)=>Offer[])=[],sent:AssistRequest|undefined;
  const session=new AssistSession("extra","h2-extra-proof",async r=>{
    sent=r;
    const active=typeof offers==="function"?offers(r):offers;
    return {form:"extra",source:"llm",request_id:r.request_id,warnings:[],fields:formKeys("extra").map(field=>{
      const offer=active.find(f=>f.field===field);
      return offer?{field,action:"set",value:null,evidence:r.text,source:"llm",status:"valid",alternatives:[],origin_request_id:r.request_id,intent_kind:"explicit_change",value_source:"utterance",...offer}
        :{field,action:"keep",value:null,evidence:"",source:"rule",status:"valid",alternatives:[],origin_request_id:r.request_id,intent_kind:"no_mention",value_source:null};
    })};
  });
  return {session,current,async send(text:string,fields:Offer[]|((r:AssistRequest)=>Offer[])){
    offers=fields;
    const before=structuredClone(current);
    assert(session.send(text,current,opts,()=>{},()=>({current,manualVersions:{}})));
    assert(!session.apply(current,()=>assert.fail("pending response must not apply")));
    await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(session.view.phase,"ready");
    assert.deepEqual(current,before);
    assert(sent);
    return sent;
  },apply(expected:AssistCurrent){
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,changes=>{patch=structuredClone(changes);Object.assign(current,changes)}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);
  }};
}
for(const text of [...new Set(positiveCases.map(c=>c.text))])test("full session exact text offer: "+text,async()=>{
  const h=harness(),offers:Offer[]=positiveCases.filter(c=>c.text===text).map(({field,value,evidence})=>({field,value,evidence}));
  try{
    const expected:AssistCurrent=Object.fromEntries(offers.map(o=>[o.field,o.value!]));
    const other=offers[0].field==="title"?"reason":"title";
    if(!offers.some(o=>o.field===other))offers.push({...offers[0],field:other});
    if(text.includes("72만원")){offers.push({field:"amount",value:720000,evidence:"추가 금액은 72만원으로 확정"});expected.amount=720000;}
    await h.send(text,offers);
    h.apply(expected);
  }finally{h.session.dispose()}
});
for(const c of denied)test("session rejects unsupported fresh offer: "+c.text,async()=>{
  const h=harness();
  try{
    await h.send(c.text,[{field:c.field,value:c.value,evidence:c.evidence}]);
    h.apply({});
  }finally{h.session.dispose()}
});
test("preview stays unapplied and the next request retains its original exact value",async()=>{
  const h=harness();
  try{
    const first=await h.send("사유 '도면 누락'으로 수정안을 먼저 보여 주세요.",[{field:"reason",value:"도면 누락",evidence:"사유 '도면 누락'으로 수정안"}]);
    assert.equal(h.current.reason,"현장 협의");
    const next=await h.send("금액도 47만원으로 변경하고, 방금 제안한 사유는 유지해 주세요.",r=>{
      const prior=r.previous_candidate,old=prior?.fields.find(f=>f.field==="reason");
      assert(prior&&old);
      assert.equal(old.value,"도면 누락");
      assert.equal(prior.origins?.reason,first.request_id);
      return [{...old,source:prior.sources?.reason,origin_request_id:prior.origins?.reason,intent_kind:"retain_previous",value_source:"previous",status:"valid",alternatives:[]},
        {field:"amount",value:470000,evidence:"금액도 47만원으로 변경"}];
    });
    assert.equal(next.previous_candidate?.origins?.reason,first.request_id);
    h.apply({amount:470000,reason:"도면 누락"});
  }finally{h.session.dispose()}
});
