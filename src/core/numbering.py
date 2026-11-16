"""Shared formatters for canonical beam span and source-object identifiers."""

from __future__ import annotations


def canonical_span_id(story: str, beam_type: str, line: int, span: int) -> str:
    return f"{story}-{beam_type}{line}-{span}"


def canonical_object_part_id(span_id: str, part_index: int) -> str:
    if part_index < 1:
        raise ValueError("Object part index must be a positive integer.")
    return f"{span_id}.{part_index}"
