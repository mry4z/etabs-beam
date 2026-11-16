"""Lossless compact encoding of failed physical-layout template keys."""
from __future__ import annotations

from array import array
import base64
import gzip
import json
import sys

from .layout_dictionary import LayoutDictionaryError, _layout_record

FORMAT = "beam-geometry-templates-compact@2"


def _code(rows, atoms, base):
    if not 1 <= len(rows) <= 3:
        return None
    value = 1
    for row in rows:
        digit = atoms.get(row)
        if digit is None:
            return None
        value = value * base + digit
    return value


def build_data(templates):
    groups = {}
    success = []
    for (prefix, hoop, rows), value in templates.items():
        if not isinstance(value, str):
            success.append([prefix, hoop, [list(row) for row in rows], _layout_record(value, 0.0)])
        else:
            group = groups.setdefault((prefix, hoop), [])
            group.append((rows, value))
    encoded = []
    for (prefix, hoop), records in groups.items():
        atom_values = sorted({atom for rows, _ in records for atom in rows})
        atoms = {atom: i + 1 for i, atom in enumerate(atom_values)}
        base = len(atoms) + 1
        if base ** 4 > 2 ** 32:
            raise LayoutDictionaryError("DICT_CODEC_ATOM_DOMAIN_TOO_LARGE")
        errors = {}
        for rows, error in records:
            code = _code(rows, atoms, base)
            if code is None:
                raise LayoutDictionaryError("DICT_CODEC_REQUIRES_ONE_TO_THREE_ROWS")
            errors.setdefault(error, []).append(code)
        blocks = []
        for error, codes in errors.items():
            numbers = array("I", sorted(codes))
            if numbers.itemsize != 4:
                raise LayoutDictionaryError("DICT_CODEC_UINT32_UNAVAILABLE")
            if sys.byteorder != "little":
                numbers.byteswap()
            blocks.append([error, base64.b64encode(numbers.tobytes()).decode("ascii")])
        encoded.append({"prefix": prefix, "hoop": hoop,
                        "atoms": [list(atom) for atom in atom_values], "errors": blocks})
    return {"format": FORMAT, "success": success, "failures": encoded}


def write_data(path, data):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"), allow_nan=False)


class FailureBank:
    def __init__(self, groups, *, normalize=lambda value: value):
        self.raw_groups = groups
        self.groups = {}
        for group in groups:
            key = (normalize(group["prefix"]), int(group["hoop"]))
            if key in self.groups:
                raise LayoutDictionaryError("DICT_CODEC_DUPLICATE_GROUP")
            atoms = {tuple(atom): i + 1 for i, atom in enumerate(group["atoms"])}
            base = len(atoms) + 1
            failures = {}
            for error, encoded in group["errors"]:
                raw = base64.b64decode(encoded, validate=True)
                numbers = array("I")
                numbers.frombytes(raw)
                if numbers.itemsize != 4:
                    raise LayoutDictionaryError("DICT_CODEC_UINT32_UNAVAILABLE")
                if sys.byteorder != "little":
                    numbers.byteswap()
                for code in numbers:
                    if code in failures:
                        raise LayoutDictionaryError("DICT_CODEC_DUPLICATE_SIGNATURE")
                    failures[code] = error
            self.groups[key] = (atoms, base, failures)

    def find(self, key):
        prefix, hoop, rows = key
        group = self.groups.get((prefix, int(hoop)))
        if group is None:
            return None
        atoms, base, failures = group
        value = _code(rows, atoms, base)
        return None if value is None else failures.get(value)
