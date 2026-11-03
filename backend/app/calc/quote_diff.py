"""업체 금액과 표준견적서 금액의 차이."""

from decimal import ROUND_HALF_UP, Decimal


def percent_vs(amount: int, standard: int) -> Decimal:
    """표준 대비 몇 % 많은지(음수면 적음). 소수 한 자리에서 반올림한다."""
    if standard <= 0 or amount < 0:
        raise ValueError("금액은 0보다 커야 해요")
    pct = (Decimal(amount) - Decimal(standard)) / Decimal(standard) * 100
    return pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
