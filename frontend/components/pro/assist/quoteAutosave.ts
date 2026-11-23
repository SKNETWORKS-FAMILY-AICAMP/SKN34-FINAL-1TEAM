// 견적 폼은 실제 금액·부가세 변경일 때만 임시 저장해요. 최초 표시와 effect 재실행은 변경이 아니에요.
export type QuoteSnapshot = { amount: string; vat: string | null };

export function shouldAutosaveQuote(previous: QuoteSnapshot, latest: QuoteSnapshot): boolean {
  return (previous.amount !== latest.amount || previous.vat !== latest.vat) &&
    !!latest.amount && !!latest.vat && Number(latest.amount) <= 1_000_000_000;
}
