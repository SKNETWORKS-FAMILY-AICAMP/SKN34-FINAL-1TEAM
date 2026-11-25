"""Sequential whitespace normalization with source offsets."""


def compact_whitespace(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs and retain every original boundary."""
    chars: list[str] = []
    offsets = [0]
    in_space = False
    for index, char in enumerate(text, 1):
        if char.isspace():
            if in_space:
                offsets[-1] = index
            else:
                chars.append(" ")
                offsets.append(index)
            in_space = True
        else:
            chars.append(char)
            offsets.append(index)
            in_space = False
    return "".join(chars), offsets


def skip_space(text: str, start: int) -> int:
    while start < len(text) and text[start].isspace():
        start += 1
    return start


def skip_space_back(text: str, end: int) -> int:
    while end > 0 and text[end - 1].isspace():
        end -= 1
    return end
