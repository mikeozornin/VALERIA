"""Line-based, bounded excerpts of saved GitLab job traces."""

from typing import Any


def validate_selection(
    limit: int | None, pattern: str | None, context_before: int, context_after: int
) -> None:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if context_before < 0 or context_after < 0:
        raise ValueError("context_before and context_after must be non-negative")
    if pattern == "":
        raise ValueError("pattern must be a non-empty literal substring")
    if pattern is None and (context_before or context_after):
        raise ValueError("context_before/context_after require pattern")


def select_trace(
    text: str,
    max_chars: int,
    offset: int | None,
    limit: int | None,
    pattern: str | None,
    context_before: int,
    context_after: int,
) -> dict[str, Any]:
    """Select LF-delimited lines; offsets address the merged search result."""
    # splitlines() treats CR progress updates and ANSI control bytes as new lines.
    lines = text.split("\n") if text else []
    if text.endswith("\n"):
        lines.pop()
    matches = None
    if pattern is None:
        selected = list(range(len(lines)))
    else:
        matches = 0
        intervals: list[list[int]] = []
        for index, line in enumerate(lines):
            if pattern not in line:
                continue
            matches += 1
            start = max(0, index - context_before)
            end = min(len(lines), index + context_after + 1)
            if intervals and start <= intervals[-1][1]:
                intervals[-1][1] = max(intervals[-1][1], end)
            else:
                intervals.append([start, end])
        selected = [index for start, end in intervals for index in range(start, end)]
    eligible = len(selected)
    start = offset or 0
    start = max(0, eligible + start) if start < 0 else min(start, eligible)
    window = selected[start : None if limit is None else start + limit]
    parts: list[str] = []
    numbers: list[int] = []
    remaining = max(0, max_chars)
    char_truncated = False
    last_line_truncated = False
    for position, index in enumerate(window):
        part = f"{index + 1}: {lines[index]}"
        if position:
            part = "\n" + part
        if len(part) > remaining:
            char_truncated = True
            # Prefer whole lines. If the first line alone exceeds the cap, still
            # return a useful prefix; the complete line remains in trace_file.
            if not parts and remaining > len(f"{index + 1}: "):
                parts.append(part[:remaining])
                numbers.append(index + 1)
                last_line_truncated = True
            break
        parts.append(part)
        numbers.append(index + 1)
        remaining -= len(part)
    excerpt = "".join(parts)
    return {
        "trace": excerpt,
        "truncated": char_truncated or start > 0 or start + len(numbers) < eligible,
        "returned_characters": len(excerpt),
        "selection": {
            "total_lines": len(lines),
            "matched_lines": matches,
            "selected_lines": eligible,
            "returned_lines": len(numbers),
            "offset": start,
            "next_offset": start + len(numbers) if start + len(numbers) < eligible else None,
            "character_limit_reached": char_truncated,
            "last_line_truncated": last_line_truncated,
            "line_numbers": "1-based prefixes in trace; offsets are 0-based selected lines",
        },
    }
