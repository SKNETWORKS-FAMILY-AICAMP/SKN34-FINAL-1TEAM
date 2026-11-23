import { createInterface } from "node:readline";
import { AssistSession } from "../../../frontend/components/pro/assist/session.ts";

const input = createInterface({ input: process.stdin, crlfDelay: Infinity })[Symbol.asyncIterator]();
const emit = (message: unknown) => console.log(JSON.stringify(message));
async function read() {
  const line = await input.next();
  if (line.done) throw new Error("시나리오 입력이 중간에 끝났습니다");
  return JSON.parse(line.value);
}
while (true) {
  const command = await read();
  if (command.stop) break;
  const session = new AssistSession(command.form, command.resource, async request => {
    emit({ request });
    return await read();
  });
  try {
    for (let i = 0; i < command.turns; i++) {
      const turn = await read();
      let finish!: () => void;
      const ready = new Promise<void>(resolve => { finish = resolve; });
      const previousId = session.view.response?.request_id;
      const unsubscribe = session.subscribe(view => {
        if (view.phase === "ready" && view.response?.request_id !== previousId) finish();
      });
      if (!session.send(turn.text, turn.current, command.options)) throw new Error("전송 실패");
      await ready;
      unsubscribe();
      const response = session.view.response!;
      const changes: Record<string, unknown> = {};
      // 마지막 턴에서만 모든 유효 후보를 명시적으로 선택한다. 충돌 대안을 임의 선택하지 않는다.
      if (i === command.turns - 1) {
        for (const field of response.fields) {
          if (field.status === "valid" && ["set","clear"].includes(field.action)) session.select(field.field, true);
        }
        session.apply(turn.current, patch => Object.assign(changes, patch));
      }
      emit({ fields: response.fields, changes, message: session.view.error });
    }
  } finally { session.dispose(); }
}
