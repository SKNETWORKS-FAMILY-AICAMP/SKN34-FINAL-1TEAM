"""명시한 편집을 실제 폼 값에 적용해서 기대 상태를 만든다."""

from copy import deepcopy


def canonical(field, value):
    if isinstance(value, list):
        return sorted(value)
    if value in (None, ""):
        return None
    return value


def equal(field, a, b, form):
    if field == "excluded" or (form == "dates" and field == "days"):
        return sorted(a or []) == sorted(b or [])
    return canonical(field, a) == canonical(field, b)


def advance(current, expected, operations):
    """수동 편집은 전송 전 현재 폼에도 반영한다. 후속 발화는 미확정 기대 후보를 이어간다."""
    current, expected = deepcopy(current), deepcopy(expected)
    for op, field, *args in operations:
        if field not in current:
            raise ValueError(f"허용되지 않은 필드 {field}")
        if op == "manual":
            current[field] = expected[field] = args[0]
        elif op == "set":
            expected[field] = args[0]
        elif op in ("hold", "reset"):
            expected[field] = current[field]
        elif op == "clear":
            expected[field] = None
        elif op == "replace":
            old, new = args
            days = expected[field]
            if not days or days.count(old) != 1 or new in days:
                raise ValueError("교체할 기존 원소와 새 원소를 확인하세요")
            expected[field] = [new if day == old else day for day in days]
        elif op != "previous":
            raise ValueError(f"알 수 없는 편집 {op}")
    return current, expected


def compare(form, current, expected, changes):
    wrong, missed, correct = [], [], []
    for field, initial in current.items():
        target = expected[field]
        actual = changes.get(field, initial)
        matches = equal(field, actual, target, form)
        needs_change = not equal(field, initial, target, form)
        changed = not equal(field, initial, actual, form)
        if needs_change and matches:
            correct.append(field)
        if not matches and changed:
            wrong.append(field)
        if not matches and needs_change:
            missed.append(field)
    return {"wrong": wrong, "missed": missed, "correct": correct}
