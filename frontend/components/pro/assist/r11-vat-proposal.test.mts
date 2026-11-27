// VAT 후보 내용 인용형: 독립 FE 검증 및 실제 Apply 경계.
import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type RuleOptions } from "./protocol.ts";
import { ruleFields } from "./rules.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2027-01-01",requiredDays:2,daysMax:365,crewMax:6,vatModes:["included","excluded","exempt"]};
const cases:readonly (readonly [AssistForm,string,string,boolean])[]=[
  ["extra","추가 금액은 84만원, 부가세는 별도라는 후보를 만들어 주세요.","부가세는 별도",true],
  ["quote","부가세는 별도라는 후보안을 작성해 주세요.","별도",true],
  ["extra","세금 조건은 별도라는 초안을 보여 주세요.","세금 조건은 별도",true],
  ["quote","부가세 구분은 별도라는 제안안을 만들어 주세요.","부가세 구분은 별도",true],
  ["extra","가능하면 부가세는 별도라는 후보를 만들어 주세요.","부가세는 별도",false],
  ["quote","부가세는 별도라는 후보를 만들어도 될까요?","부가세는 별도",false],
  ["extra","부가세는 별도라는 후보를 만들지 마세요.","부가세는 별도",false],
  ["quote","부가세는 별도라는 후보를 검토해 주세요.","부가세는 별도",false],
  ["extra","부가세는 별도라는 후보를 만들어 주세요. 부가세 변경은 취소합니다.","부가세는 별도",false],
  ["quote","부가세는 별도라는 후보를 만들어 주세요. 부가세는 포함으로 확정해 주세요.","부가세는 별도",false],
  ["extra","사유는 별도라는 후보를 만들어 주세요.","별도",false],
  ["extra",'제목은 "부가세는 별도라는 후보를 만들어 주세요"로 적어 주세요.',"부가세는 별도",false],
  ["quote","운반비는 별도라는 후보를 만들어 주세요.","별도",false],
  ["quote","별도라는 후보를 만들어 주세요.","별도",false],
];

for (const [form,text,evidence,accepted] of cases) {
  test("VAT proposal: "+text,async()=>{
    const current:AssistCurrent=form==="quote"
      ?{amount:3260000,vat:"included",days:4,valid:"2027-01-27"}
      :{title:"기존 보강",reason:"기존 손상",amount:250000,vat:"included"};
    if(accepted)assert.equal(ruleFields(form,text,opts).find(x=>x.field==="vat")?.value,"excluded");
    const session=new AssistSession(form,"00000000-0000-4000-8000-000000000179",async(r:AssistRequest)=>({
      form,request_id:r.request_id,source:"llm",warnings:[],
      fields:formKeys(form).map(field=>({
        field,action:"keep",status:"valid",value:null,evidence:"",source:"rule",
        intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id,
        alternatives:[],correction:null,reason_code:null,
        ...(field==="vat"?{action:"set",value:"excluded",evidence,source:"llm",
          intent_kind:"explicit_change",value_source:"utterance"}:{})
      })),
    }));
    let done!:()=>void;
    const ready=new Promise<void>(resolve=>{done=resolve;});
    const off=session.subscribe(v=>{if(v.phase==="ready"&&v.utterance===text)done();});
    assert(session.send(text,current,opts,()=>{},()=>({current:structuredClone(current),manualVersions:{}})));
    assert.equal(session.apply(current,()=>assert.fail("응답 전 적용")),false);
    await ready;off();
    const vat=session.view.response!.fields.find(f=>f.field==="vat")!;
    assert.equal(vat.status==="valid"&&vat.action==="set"&&vat.value==="excluded",accepted);
    assert.equal(current.vat,"included");
    for(const key of formKeys(form))session.select(key,key==="vat");
    let patch:AssistCurrent={};
    assert.equal(session.apply(current,v=>{patch=structuredClone(v);}),accepted);
    assert.deepEqual(patch,accepted?{vat:"excluded"}:{});
  });
}
