// 승인 업체 전용 해석 요청. 응답은 미리보기이며 이 경로에서는 업무 데이터를 저장하지 않아요.
import type { AssistRequest } from "./protocol";

export class AssistApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function interpretAssist(request: AssistRequest, signal: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch("/api/vendor/assist/interpret", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new AssistApiError(0, "network", "서버에 연결하지 못했어요.");
  }
  const data: unknown = await response.json().catch(() => null);
  if (response.ok) return data;
  const payload = data && typeof data === "object" ? data as Record<string, unknown> : null;
  const info = payload?.error && typeof payload.error === "object" ? payload.error as Record<string, unknown> : null;
  const message = typeof info?.message === "string"
    ? info.message
    : response.status === 401 ? "로그인한 뒤 이용해 주세요."
      : response.status === 403 ? "이 요청을 이용할 권한이 없어요."
        : response.status === 429 ? "요청이 많아요. 잠시 뒤 다시 보내 주세요."
          : "해석 요청을 처리하지 못했어요.";
  throw new AssistApiError(response.status, typeof info?.code === "string" ? info.code : "unknown", message);
}
