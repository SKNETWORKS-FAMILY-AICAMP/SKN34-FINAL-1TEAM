# 업체 도우미 #178 고정 평가

기존 `parse.ts` 규칙과 `assist_service.interpret` 하이브리드 응답을 같은 문장·문맥에서 비교한다. 평가 코드에는 별도 자연어 추출 규칙이 없다.

## 데이터와 검수 상태

| 화면 | dev | heldout | 전체 |
| --- | ---: | ---: | ---: |
| 실측 가능 기간(`period`) | 24 | 12 | 36 |
| 실측 견적서(`quote`) | 24 | 12 | 36 |
| 공사 날짜·인원(`dates`) | 24 | 12 | 36 |
| 추가 금액(`extra`) | 24 | 12 | 36 |
| **합계** | **96** | **48** | **144** |

**현재 144개 정답은 모두 AI가 작성한 검수 전 초안이다.** 모든 줄의 `gold_origin`은 `ai_draft`, `human_verified`는 `false`, `reviewed_by`는 `null`이다. 기존 QA 문장을 포함하되, 외부 사람이 원문·의도·필드별 기대값을 확인하지 않았다. Chat Pro의 별도 검토 명세·20건 라벨 제안은 독립 판단·후보 익명 비교·순서 반전의 LLM-as-a-Judge 방식으로 요청된 **구조적 AI 검토 증거**이며 사람이 승인한 정답이 아니다. 여기서 나온 수치는 모델 품질 승인 또는 실사용 정확도의 근거가 아니다.

각 줄은 `id`, `split`, `form`, `kind`, `tags`, `text`, `today`, `base_month`, `current`, `previous_candidate`, `context`, `expected`를 가진다. `expected`는 **화면의 모든 필드**에 `action`(`keep|set|clear|uncertain`)과 `value`를 명시한다. `set` 외 액션의 값은 `null`이다. 이전 후보는 이전 원문과 원문에 실제 등장한 근거를 포함한다. 날짜 기준은 2026-11-23, 기준 월은 2026-12-01로 고정했다.

문장 종류 12가지(`simple`, `multi_field`, `partial`, `negation`, `double_negation`, `correction`, `previous_correction`, `previous_keep`, `clear`, `ambiguous`, `conflict`, `no_action`)가 화면마다 3개씩 있다. 금액·기간 정정, 부가세 부정, 여러 날 제외 사례는 추가 `tags`로도 분류한다.

`make_dataset.py`의 각 사례와 기대 액션은 검수·수정 전까지 변하지 않도록 보관한다. 재생성 명령은 다음과 같다.

```bash
python evaluation/vendor_assist/make_dataset.py
python -m pytest -q evaluation/tests/test_vendor_assist_eval.py
```

사람이 직접 검토할 때는 문장에 대해 실제 원하는 폼 변화를 확인하고 정답과 검수 상태를 함께 갱신해야 한다. AI 초안을 임의로 `human_verified=true`로 바꾸지 않는다.

## 실행

저장소 루트에서 실행한다. Node는 저장소 요구 버전(24.18 이상)을 권장한다. 22.23에서는 `--experimental-strip-types`로 확인했다.

```bash
# 기존 기록과 비교할 원본 TS 파서만 진단, dev, 유료 호출 0회
python evaluation/vendor_assist/run.py --mode raw_rule --split dev

# 신규 기본값: 동일 서버 validator·merge 경로의 규칙 단독 평가 (모델 호출 0회)
python evaluation/vendor_assist/run.py --mode rule --split dev

# 서버 순수 해석 서비스 경유, 오프라인 스텁/규칙 대체 (실제 LLM 품질 측정 아님)
python evaluation/vendor_assist/run.py --mode hybrid --split dev

# 선택한 dev 실행만 로컬 JSONL에 단계 기록 (기본적으로 기록하지 않음)
python evaluation/vendor_assist/run.py --mode rule --split dev --trace-jsonl evaluation/vendor_assist/results/local-dev-stage-trace.jsonl

# held-out은 최종 평가에서만 허가한다
python evaluation/vendor_assist/run.py --mode rule --split heldout --final

# 상세 결과 JSON 저장
python evaluation/vendor_assist/run.py --mode rule --split dev --json evaluation/vendor_assist/rule-dev-report.json
```

`raw_rule`은 TS 파서 원시 출력 진단이다. 신규 `rule`과 `hybrid`는 `assist_service.interpret(data, context, *, use_model=False/True, trace=None)`의 **동일한 검증·이전 후보 상속·병합·최종 조합 검사** 경로를 사용한다. `rule`은 모델만 비활성화하며, `hybrid`는 모델의 후보도 병합한다. 실제 업무 API 라우터를 호출하지 않고 고정 UUID·날짜·제약으로 `AssistRequest`/`AssistContext`를 만든다. 규칙 후보는 같은 TS 파서에서 주입하므로 원시 파서 점수와 새 서버 규칙 점수를 구별한다. `--parser-path`는 비교 대상 파서 원본을 읽기만 한다.

`--trace-jsonl`을 명시한 **dev 평가에서만** 서버가 생성한 `rule_raw`, `model_raw`, `previous_verified`, `rule_reviewed`, `model_reviewed`, `merged`, `final` 및 `decisions/reasons`를 사례별 JSONL 로컬 파일로 남긴다. 옵션이 없으면 trace 객체를 생성하지 않고 파일도 쓰지 않는다. production 로그·API 응답에는 기록하지 않는다. 원문 근거가 포함될 수 있으므로 공유용 결과에서 별도 관리한다. 알려진 held-out 경로는 **파일을 읽기 전에** `--final` 여부를 확인하고, trace는 `--final`을 주더라도 held-out에서 금지한다. 이름을 바꾼 자료는 기존 split 메타데이터 확인으로 추가 차단한다.

`--live`를 주지 않으면 외부 `LLM_LIVE=true` 환경변수가 있어도 `hybrid` 실행에서 강제로 `LLM_LIVE=false`가 된다. **유료 호출은 명시적인 `--mode hybrid --live`에서만 가능하다.** 현재처럼 사람이 검수하지 않은 dev 문장으로 진단할 경우 `--allow-unreviewed`까지 명시해야 한다. `heldout`에는 이 옵션을 사용할 수 없다. 실모델 진단 수치도 정답 검수가 끝나기 전까지 최종 성능이라고 보고하지 않는다.

```bash
# 사람이 아직 검수하지 않은 dev의 유료 진단 허가 (사용자가 비용 확인 후 수동 실행)
python evaluation/vendor_assist/run.py --mode hybrid --split dev --live --allow-unreviewed
```

## 지표 의미

단일 적용 후보 오류율은 `status=valid`인 `set/clear`를 센다. 충돌 대안은 별도 확인 대상이므로 충돌 필드 수·대안 수·단일 정답과 다른 대안 수도 따로 기록한다. 단일 후보 오류가 0건이어도 모든 충돌 대안이 정답이라는 뜻은 아니다.

| 지표 | 계산 |
| --- | --- |
| 필드 정확도 | 필드별 `action`과 `value`를 함께 맞힌 수 / 전체 필드 수 |
| 문장 전체 정확도 | 화면의 모든 필드를 맞힌 문장 수 / 문장 수 |
| 잘못된 후보율 | 실제 적용 가능한 `set|clear` 중 정답 액션·값과 다른 후보 수 / 반환한 적용 가능 후보 수 |
| 미추출률 | 정답 `set|clear` 중 동일한 액션·값을 반환하지 못한 수 / 정답 적용 가능 필드 수 |
| 실제 변경 정밀도·재현율 | `case.current`의 **명시된 값**과 비교해 값이 실제 달라지는 `set/clear`만 TP·FP·FN으로 분류; 잘못된 최종값은 FP와 FN 모두 계산 |
| 동일 값 재확인 | `set`이 현재값과 같거나 `clear` 대상이 이미 비어 있으면 noop; 정답·예측·일치·불필요 재확인 각각 기록 |
| 실제 불확실성·충돌·검증 실패 | `gold uncertain`의 유효한 보류와 `status=conflict|invalid`, 위험 `set/clear`를 별도로 집계; 충돌 대안이 정답이어도 자동 적용 TP 아님 |
| baseline 누락 | `case.current`에 해당 필드가 없으면 실제 변경 채점에서 제외하며 무조건 `null`로 추정하지 않음. 과거 `previous_candidate.baselines` 누락은 별도 계수 |
| 부정·정정·복합 정확도 | `kind`, `tags`별 문장 전체 정확도 |
| 응답 지연 | 문장별 실제 파서 또는 서비스 소요의 평균·p50·p95 |
| 토큰·비용 | 실제 수집된 입력·출력·캐시 토큰과 사용자가 명시한 토큰 요금만 사용 |
| 서비스 오류·시간초과 | 서비스 응답 자체가 없는 사례, 유효하지 않은 출력 또는 평가 제한 시간초과 건수 |
| 모델 경로 오류·시간초과 | 서비스가 규칙 후보로 복구한 경우에도 내부 LLM 호출에서 실제 발생한 예외/시간초과 건수 |

잘못된 `set` 값은 **오후보와 올바른 값 미추출에 동시에** 포함한다. `keep`은 삭제가 아니다. 충돌·유효하지 않은 후보는 자동 적용 후보로 세지 않는다. 필드별 정확도는 미언급 `keep`의 영향을 받으므로 문장 전체 정확도와 오후보·미추출률을 함께 비교한다. 날짜 배열은 순서만 무시하고, 중복 날짜는 허용하지 않는다. 실변경 비교에서는 정답과 예측의 최종 **폼 값**을 비교하고 공란을 필드별로 해석한다. 기존 `case.current`는 케이스의 선언된 현재 상태이고, **직전 미확정 후보의 생성 당시 값**인 `previous_candidate.baselines`와 별개다. 과거 기록에 후자가 없으면 추정해서 만들지 않는다. 기존 `kind=conflict`는 문장 수준 분류이므로 검수된 필드별 충돌 정답으로 오인하지 않는다. `source=rule`은 LLM 비활성·실패 시 대체·규칙 채택을 모두 포함하므로, 별도 `llm_error`, `llm_timeout`, `adapter_invoked`와 `llm_live` 기록으로 구분한다. `adapter_invoked`는 SDK 어댑터에 진입한 건수이며 유료 네트워크 요청 수를 뜻하지 않는다.

요금은 모델·가격이 확인됐을 때에만 다음 인자를 직접 입력한다: `--input-price`, `--output-price`, `--cached-price`(각각 **100만 토큰당 USD**). 가격이 누락되거나 사용량이 미계측이면 **비용은 `null`**이다. 입력·출력·캐시 토큰 값도 수집하지 못하면 `null`이며, 추정값을 0으로 채우지 않는다. 서비스의 `llm.structured` / `vendor_assist.interpret` 선택적 `usage` 인자를 통해 계측한다.

`--data`로 파일 이름을 변경해 지정해도 내부 `split=heldout`이면 `--final` 없이는 실행되지 않는다. dev 결과로 프롬프트·코드를 수정한 뒤, held-out은 최종 고정 버전으로 한 번만 확인한다. 평가 테스트는 개발·합성 데이터(`test_vendor_assist_eval.py`, `test_vendor_assist_quality_eval.py`)와 전체 정합성(`test_vendor_assist_corpus.py`)을 분리했다. #179 개발 중에는 앞의 두 파일만 실행하며 전체 corpus 검사는 승인된 최종 절차에서 수행한다.

## 2026-11-23 개발 진단

평가 코드 기준은 `6343394bd25ef693c04c32d46480913572b28906`이다. #176 `d351d22`의 파서를 그대로 사용했다. **정답이 사람 검수 전인 dev 96문장 진단**이며 held-out 48문장은 실행하지 않았다. [측정 요약과 해시](results/dev-2026-11-23.json)에 필드별 결과·토큰·단가 출처·제약을 기록했다.

| 방식 | 필드 일치 | 문장 전체 일치 | 잘못된 단일 후보 | 충돌 필드 | 평균 시간 | 96건 비용(USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 원시 규칙 파서(`raw_rule`, 기존 기록) | 216/312 (69.2%) | 27/96 | 36 | 0 | 0.073ms | 유료 호출 없음 |
| Terra / medium | 273/312 (87.5%) | 59/96 | 0 | 9 | 3.352초 | 0.06870015 |
| Luna / medium | 272/312 (87.2%) | 61/96 | 1 | 9 | 2.238초 | 0.00889745 |

두 모델은 서비스·모델 오류와 시간초과가 각각 0건이었다. 각 모델의 충돌 9필드에는 총 18개 대안이 있으며, 이 중 미검수 단일 정답과 다른 대안은 9개다. 충돌은 사용자가 값을 고르기 전 적용할 수 없다. Luna의 단일 후보 불일치는 ‘추가 금액이 10만 원 정도 될 것 같아요’를 `uncertain` 대신 100,000원 후보로 반환한 사례다. 검수 전 정답과의 차이로 기록하며 의미 판단의 최종 정답이라고 단정하지 않는다.

지연은 규칙 함수 또는 해석 서비스 실행 시간이다. 브라우저·인증·업무 조회까지 포함한 시간은 아니다. 두 모델은 별도 프로세스에서 동시에 측정했고 캐시 입력 토큰이 포함된다. 비용은 확인한 [Terra 단가](https://developers.openai.com/api/docs/models/gpt-6.1-terra)와 [Luna 단가](https://developers.openai.com/api/docs/models/gpt-6-luna)를 실제 사용량에 적용한 계산값이며 청구서 금액은 아니다.

위 기존 기록은 원시 규칙 파서와 하이브리드의 **검증 경로가 달랐으므로 동일 기준 성능 비교가 아니다**. 초기 설정 `gpt-6.1-terra/medium`을 유지한다. 사람의 정답 검수·held-out 최종 평가가 끝나기 전에는 모델 선정을 확정하거나 이 진단을 서비스의 보장 정확도로 사용하지 않는다.
