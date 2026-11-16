"""Exact physical-layout catalog compiled once per complete candidate context."""
from __future__ import annotations

import math
import gzip
import lzma
from bisect import bisect_right
from collections import OrderedDict
import sys
import struct
import json
from pathlib import Path
from dataclasses import fields, replace
from functools import lru_cache
from typing import Any

from .detailing import DetailingError, LongitudinalDesign, PhysicalRow
from .layout_dictionary import (
    LayoutDictionaryError, LayoutDictionaryMiss, _candidate_parameters,
    _candidate_sort_key, _layout_record, _possible_physical_rows, _bank_key,
    _last_layer_count_event_upper, _IGNORED_SECTION_CONTEXT_FIELDS,
    normalized_bank_context_for, _engine_context, _canonical,
)
from .row_candidates import _is_two_row_four_three, signature
from .offline_style import DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R9_ID


def _normalize_template_prefix(prefix):
    parts = json.loads(prefix)
    parts[:3] = [float(value) for value in parts[:3]]
    return _canonical(parts)


def _code_fingerprint(engine):
    code = engine.code
    base = getattr(code, "base_policy", code)
    return (type(code), type(base), base, str(getattr(code, "spacing_rule_id", "")))


@lru_cache(maxsize=32)
def _physical_section_fields(section_type):
    return tuple(field.name for field in fields(section_type)
                 if field.name not in _IGNORED_SECTION_CONTEXT_FIELDS)


def _section_fingerprint(section):
    names = _physical_section_fields(type(section))
    values = (getattr(section, name) for name in names)
    return names, tuple((float, struct.pack("!d", value)) if type(value) is float else (type(value), value)
                        for value in values)


def _policy_fingerprint(engine):
    return (str(engine.detailing_policy_id), str(engine.detailing_policy_revision_id),
            bool(engine.avoid_two_row_4_3), bool(engine.uniform_stirrup_diameter_per_span),
            bool(engine.exact_candidate_state_dedup), engine.project, _code_fingerprint(engine))


_JSON_DECODER = json.JSONDecoder()


def _jsonl_leading_key(line):
    """Read a standard index line's leading key; return None for other formats."""
    def whitespace(i):
        while i < len(line) and line[i] in " \t\r\n":
            i += 1
        return i
    try:
        i = whitespace(0)
        if i >= len(line) or line[i] != "{":
            return None
        field, i = _JSON_DECODER.raw_decode(line, whitespace(i + 1))
        if field != "key":
            return None
        i = whitespace(i)
        if i >= len(line) or line[i] != ":":
            return None
        key, i = _JSON_DECODER.raw_decode(line, whitespace(i + 1))
        if not isinstance(key, str):
            return None
        i = whitespace(i)
        if i >= len(line) or line[i] != ",":
            return None
        field, i = _JSON_DECODER.raw_decode(line, whitespace(i + 1))
        if field != "membership":
            return None
        i = whitespace(i)
        if i >= len(line) or line[i] != ":":
            return None
        i = whitespace(i + 1)
        if (i >= len(line) or line[i] != "["
                or not line.endswith(("]}", "]}\n", "]}\r\n"))
                or line.find('"', i) != -1):
            return None
        return key
    except (ValueError, IndexError):
        return None


def context_key(section, hoops, engine, *, first_diameter=None, max_layers=None,
                face="top", preserve_support_alternatives=True):
    context = normalized_bank_context_for(
        section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers,
        face=face, preserve_support_alternatives=preserve_support_alternatives)
    if context["engine"]["detailing_policy_revision_id"] == DRAWING_POLICY_REVISION_R9_ID:
        context["engine"]["detailing_policy_revision_id"] = DRAWING_POLICY_REVISION_R7_ID
    return _bank_key(context)


def build_context(section, hoops, engine, *, first_diameter=None, max_layers=None,
                  face="top", preserve_support_alternatives=True, geometry_resolver=None):
    """Resolve physical layouts exactly once; preserve failed nodes as absent.

    Candidate membership is derived from these immutable records and the exact
    oracle prefix stopping rules; no geometry is computed at query time.
    """
    hoops = tuple(hoops)
    if not hoops:
        raise LayoutDictionaryError("DICTIONARY_CONTEXT_REQUIRES_HOOPS")
    first, diameters, minimum_count, count_bound, limit = _candidate_parameters(
        section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers)
    records = {}
    geometry = {}
    # The 4/3 rewrite can produce five bars even if the nominal count bound is
    # four. Such a row is still resolved by the oracle and must be represented.
    rows_iter = list(_possible_physical_rows(section, hoops, engine,
                                            first_diameter=first_diameter, max_layers=max_layers))
    if engine.avoid_two_row_4_3 and limit >= 2:
        for outer in diameters:
            for inner in diameters:
                replacement = (PhysicalRow(5, outer), PhysicalRow(2, inner))
                rows_iter.append(replacement)
                if limit >= 3:
                    for diameter in diameters:
                        for count in range(2, count_bound + 1):
                            rows_iter.append((*replacement, PhysicalRow(count, diameter)))
    for rows in rows_iter:
        key = tuple((r.count, r.diameter_mm) for r in rows)
        if key in records:
            continue
        design = LongitudinalDesign(rows=rows, required_area_mm2=0.0,
            constraint_sources=("FINITE_PHYSICAL_ROWS", "FIRST_ROW_HOOP_SUPPORT"))
        resolved = []
        for hoop in hoops:
            geometry_key = (int(hoop.diameter_mm), key)
            try:
                resolver = geometry_resolver or engine._resolve_physical_layout
                item = resolver(section, hoop, design, face=face)
                resolved.append(item)
                geometry[geometry_key] = _layout_record(item, 0.0)
            except DetailingError as error:
                geometry[geometry_key] = {"error": str(error).replace(section.name, "{section}")}
        if len(resolved) != len(hoops):
            records[key] = None
            continue
        minimum = max(engine.code.minimum_longitudinal_area(
            section.width_mm, item.effective_depth_mm, section.fc_mpa, section.fy_mpa)
            for item in resolved)
        ratio_ok = not section.is_smf or all(
            design.provided_area_mm2 <= engine.project.smf_max_longitudinal_ratio
            * section.width_mm * item.effective_depth_mm + 1e-9 for item in resolved)
        records[key] = (_layout_record(resolved[0], minimum), ratio_ok)
    return {"key": context_key(section, hoops, engine, first_diameter=first_diameter,
                max_layers=max_layers, face=face,
                preserve_support_alternatives=preserve_support_alternatives),
            "first": first, "diameters": diameters, "minimum_count": minimum_count,
            "count_bound": count_bound, "limit": limit, "records": records, "geometry": geometry}


@lru_cache(maxsize=100000)
def _count_event(area, per_bar, count, minimum):
    return _last_layer_count_event_upper(area, per_bar, count, minimum)


def _compile_membership(context, engine, preserve_support_alternatives):
    """Propagate closed float intervals through oracle stopping conditions."""
    records = context["records"]
    diameters = context["diameters"]
    minimum_count = context["minimum_count"]
    count_bound = context["count_bound"]
    limit = context["limit"]
    dedup = bool(engine.exact_candidate_state_dedup)
    intervals = {}

    def visit(rows, low, high, extension=False):
        if low > high:
            return
        physical = tuple(PhysicalRow(count, diameter) for count, diameter in rows)
        if _is_two_row_four_three(physical, engine):
            four = next(r for r in physical if r.count == 4)
            three = next(r for r in physical if r.count == 3)
            if minimum_count <= 5:
                visit(((5, four.diameter_mm), (2, three.diameter_mm)), low, high, True)
            return
        item = records.get(rows)
        if item is None or not item[1]:
            return
        record = item[0]
        area = record["provided_area_mm2"]
        enough_high = min(high, area + 1e-9)
        if area + 1e-9 >= record["as_min_mm2"] and low <= enough_high:
            intervals.setdefault(rows, []).append((low, enough_high))
            if (preserve_support_alternatives and not extension and len(rows) < limit
                    and rows[0][0] == minimum_count):
                for diameter in diameters:
                    for count in range(2, count_bound + 1):
                        visit((*rows, (count, diameter)), low, enough_high, True)
        if len(rows) >= limit:
            return
        # If area cannot satisfy AsMin, every demand follows the shortage path.
        shortage_low = low if area + 1e-9 < record["as_min_mm2"] else max(
            low, math.nextafter(area + 1e-9, math.inf))
        if shortage_low > high:
            return
        for diameter in diameters:
            per_bar = math.pi * diameter**2 / 4.0
            for count in range(2, count_bound + 1):
                child_high = high
                if dedup and len(rows) + 1 == limit:
                    child_high = min(child_high, _count_event(area, per_bar, count, 2))
                visit((*rows, (count, diameter)), shortage_low, child_high, extension)

    for diameter in context["first"]:
        if diameter not in diameters:
            continue
        per_bar = math.pi * diameter**2 / 4.0
        for count in range(minimum_count, count_bound + 1):
            high = sys.float_info.max
            if dedup and limit == 1:
                high = _count_event(0.0, per_bar, count, minimum_count)
            visit(((count, diameter),), 0.0, high)
    events = []
    for rows, members in intervals.items():
        members.sort()
        merged = []
        for low, high in members:
            if merged and low <= math.nextafter(merged[-1][1], math.inf):
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        events.extend((low, high, rows) for low, high in merged)
    events.sort()
    return events


def _materialize(record, required):
    return LongitudinalDesign(
        rows=tuple(PhysicalRow(int(r["count"]), int(r["diameter_mm"]), str(r["role"]),
                               float(r["credited_area_mm2"])) for r in record["rows"]),
        required_area_mm2=max(required, record["as_min_mm2"]),
        clear_spacing_mm=record["clear_spacing_mm"],
        row_centers_mm=tuple(record["row_centers_mm"]),
        row_x_mm=tuple(tuple(row) for row in record["row_x_mm"]),
        effective_depth_mm=record["effective_depth_mm"],
        constraint_sources=tuple(record["constraint_sources"]))


def _v3_record_has_mirrored_subset(record):
    previous = None
    rows = tuple(record.get("rows", ()))
    row_x = tuple(record.get("row_x_mm", ()))
    if len(rows) != len(row_x):
        return False
    for slots_raw in row_x:
        slots = tuple(float(value) for value in slots_raw)
        if not slots or any(
            abs(left + right - float(record["width_mm"])) > 1e-5
            for left, right in zip(slots, reversed(slots))
        ):
            return False
        if previous is not None and any(
            not any(abs(value - outer) <= 1e-5 for outer in previous)
            for value in slots
        ):
            return False
        previous = slots
    return True


class LookupCatalog:
    """Prepared-context provider with the same query contract as the old bank."""
    def __init__(self):
        self.contexts = {}
        self._geometry_templates = {}
        self._failure_banks = []
        self._context_key_cache = {}
        self._template_prefix_cache = {}
        self._last_base_context = None
        self._query_cache = OrderedDict()
        self.query_audit = {
            "query_calls": 0, "query_cache_hits": 0, "query_cache_misses": 0,
            "context_misses": 0, "materialized_count": 0, "sort_calls": 0,
            "geometry_lookup_calls": 0, "geometry_template_hits": 0,
            "geometry_record_hits": 0, "geometry_misses": 0,
            "geometry_failures": 0, "physical_layout_computations": 0,
            "geometry_span_cache_hits": 0, "geometry_span_cache_misses": 0,
            "geometry_actual_resolves": 0, "geometry_span_cache_evictions": 0,
        }

    def metadata(self):
        """Return a point-in-time snapshot suitable for worker delta accounting."""
        return {"mode": "PRECOMPUTED_PHYSICAL_ROW_LOOKUP",
                "context_count": len(self.contexts), "query_cache_capacity": 512,
                "query_cache_entries": len(self._query_cache),
                "query_audit": dict(self.query_audit)}

    def _key(self, section, hoops, engine, *, first_diameter, max_layers, face,
             preserve_support_alternatives):
        pairs = tuple(dict.fromkeys((int(h.diameter_mm), int(h.legs)) for h in hoops))
        fast = (_section_fingerprint(section), pairs, _policy_fingerprint(engine), first_diameter, max_layers,
                face, bool(preserve_support_alternatives))
        key = self._context_key_cache.get(fast)
        if key is None:
            key = context_key(section, hoops, engine, first_diameter=first_diameter,
                max_layers=max_layers, face=face,
                preserve_support_alternatives=preserve_support_alternatives)
            self._context_key_cache[fast] = key
        return key

    def _prefix(self, section, engine):
        fast = (float(section.width_mm), float(section.clear_cover_to_stirrup_outer_mm or 0),
                float(section.aggregate_mm), _code_fingerprint(engine),
                bool(engine.exact_candidate_state_dedup),
                bool(getattr(engine, "strict_symmetric_rows", False)))
        prefix = self._template_prefix_cache.get(fast)
        if prefix is None:
            prefix_fields = [fast[0], fast[1], fast[2], _engine_context(engine)["code"], fast[4]]
            if fast[5]:
                prefix_fields.append("STRICT_MIRRORED_ROWS_V3")
            prefix = _canonical(prefix_fields)
            self._template_prefix_cache[fast] = prefix
        return prefix

    def template_prefixes(self):
        """Prefixes represented by either ordinary or compact geometry records."""
        prefixes = {key[0] for key in self._geometry_templates}
        for bank in self._failure_banks:
            prefixes.update(prefix for prefix, _hoop in bank.groups)
        return prefixes

    def _template_value(self, key):
        value = self._geometry_templates.get(key)
        if value is not None:
            return value
        for bank in self._failure_banks:
            value = bank.find(key)
            if value is not None:
                return value
        return None

    def save(self, path):
        """Save prepared records as compressed portable data."""
        if self._failure_banks:
            from .lookup_template_codec import build_data
            payload = build_data(self._geometry_templates)
            payload["extra_failure_banks"] = [bank.raw_groups for bank in self._failure_banks]
            payload["contexts"] = []
        else:
            payload = {"format": "beam-row-lookup-contexts@1", "contexts": [],
                       "geometry_templates": [[prefix, hoop, list(map(list, rows)),
                           ({"error": value} if isinstance(value, str)
                            else {"layout": _layout_record(value, 0.0)})]
                           for (prefix, hoop, rows), value in self._geometry_templates.items()]}
        for context in self.contexts.values():
            item = dict(context)
            if item.get("index_only"):
                payload["contexts"].append(item)
                continue
            item["records"] = [[list(map(list, key)), value]
                               for key, value in context["records"].items()]
            item["geometry"] = [[hoop, list(map(list, rows)), value]
                                for (hoop, rows), value in context["geometry"].items()]
            payload["contexts"].append(item)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        temporary.replace(destination)
        return destination

    def save_compact(self, path):
        """Save worker contexts as interval indices with shared templates."""
        compact = LookupCatalog()
        compact._geometry_templates = self._geometry_templates
        compact._failure_banks = self._failure_banks
        for key, context in self.contexts.items():
            if (context.get("index_only") or
                    json.loads(key)["section"].get("clear_cover_to_stirrup_outer_mm") is None or
                    json.loads(key)["section"]["depth_mm"] >= 1000000.0):
                compact.contexts[key] = context
                continue
            membership = [(low, high, rows, context["records"][rows][0]["as_min_mm2"])
                          for low, high, rows in context["membership"]]
            compact.contexts[key] = {"key": key, "membership": membership,
                "membership_starts": [entry[0] for entry in membership], "index_only": True}
        return compact.save(path)

    @classmethod
    def load(cls, path):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        from .lookup_template_codec import FORMAT, FailureBank
        format_id = payload.get("format")
        if format_id not in {"beam-row-lookup-contexts@1", FORMAT}:
            raise LayoutDictionaryError("DICT_CATALOG_FORMAT_UNSUPPORTED")
        result = cls()
        prefixes = {}
        if format_id == FORMAT:
            for prefix, hoop, rows, record in payload.get("success", []):
                if prefix not in prefixes:
                    prefixes[prefix] = _normalize_template_prefix(prefix)
                key = (prefixes[prefix], hoop, tuple(tuple(row) for row in rows))
                result._geometry_templates[key] = _materialize(record, 0.0)
            result._failure_banks.append(FailureBank(payload.get("failures", []),
                                                     normalize=_normalize_template_prefix))
            for groups in payload.get("extra_failure_banks", []):
                result._failure_banks.append(FailureBank(groups, normalize=_normalize_template_prefix))
        else:
            for prefix, hoop, rows, value in payload.get("geometry_templates", []):
                if prefix not in prefixes:
                    prefixes[prefix] = _normalize_template_prefix(prefix)
                key = (prefixes[prefix], hoop, tuple(tuple(row) for row in rows))
                result._geometry_templates[key] = (value["error"] if "error" in value
                                                   else _materialize(value["layout"], 0.0))
        for context in payload.get("contexts", []):
            if context.get("index_only"):
                context["membership"] = [(low, high, tuple(tuple(row) for row in rows), minimum)
                                         for low, high, rows, minimum in context["membership"]]
            else:
                context["records"] = {tuple(tuple(row) for row in key): value
                                      for key, value in context["records"]}
                context["membership"] = [(low, high, tuple(tuple(row) for row in rows))
                                         for low, high, rows in context["membership"]]
                context["geometry"] = {(hoop, tuple(tuple(row) for row in rows)): value
                                       for hoop, rows, value in context["geometry"]}
            if context["key"] in result.contexts:
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_CONTEXT")
            result.contexts[context["key"]] = context
        return result

    def save_indices(self, path, *, compresslevel=9):
        """Persist contextual membership separately from shared geometry templates."""
        payload = {"format": "beam-row-interval-indices@1", "contexts": []}
        for context in self.contexts.values():
            if context.get("index_only"):
                entries = context["membership"]
            else:
                entries = [(low, high, rows, context["records"][rows][0]["as_min_mm2"])
                           for low, high, rows in context["membership"]]
            payload["contexts"].append({"key": context["key"], "membership": entries})
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=compresslevel) as stream:
            stream.write(encoded)
        temporary.replace(destination)
        return destination

    def load_indices(self, path, *, only_keys=None):
        def add(item):
            key = item["key"]
            if only_keys is not None and key not in only_keys:
                return
            membership = [(low, high, tuple(tuple(row) for row in rows), minimum)
                          for low, high, rows, minimum in item["membership"]]
            existing = self.contexts.get(key)
            if existing is not None:
                if existing.get("index_only") and existing["membership"] == membership:
                    return
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_CONTEXT")
            self.contexts[key] = {"key": key, "membership": membership,
                "membership_starts": [entry[0] for entry in membership], "index_only": True}
        opener = lzma.open if Path(path).suffix.lower() == ".xz" else gzip.open
        with opener(path, "rt", encoding="utf-8") as stream:
            first = stream.readline()
            header = json.loads(first)
            if header.get("format") == "beam-row-interval-indices-jsonl@1":
                for line in stream:
                    if only_keys is not None:
                        key = _jsonl_leading_key(line)
                        if key is not None and key not in only_keys:
                            continue
                    add(json.loads(line))
            elif header.get("format") == "beam-row-interval-indices@1":
                for item in header["contexts"]:
                    add(item)
            else:
                raise LayoutDictionaryError("DICT_INTERVAL_INDEX_FORMAT_UNSUPPORTED")
        return self

    @classmethod
    def load_many(cls, paths):
        result = cls()
        for path in paths:
            part = cls.load(path)
            if result.contexts.keys() & part.contexts.keys():
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_CONTEXT")
            if result._geometry_templates.keys() & part._geometry_templates.keys():
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_GEOMETRY")
            existing_pairs = set().union(*(bank.groups.keys() for bank in result._failure_banks))
            if any(key[:2] in existing_pairs and result._template_value(key) is not None
                   for key in part._geometry_templates):
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_GEOMETRY")
            if any(existing_pairs & bank.groups.keys() for bank in part._failure_banks):
                raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_GEOMETRY")
            if part._failure_banks:
                part_pairs = set().union(*(bank.groups.keys() for bank in part._failure_banks))
                for key in result._geometry_templates:
                    if key[:2] in part_pairs and any(bank.find(key) is not None for bank in part._failure_banks):
                        raise LayoutDictionaryError("DICT_CATALOG_DUPLICATE_GEOMETRY")
            result.contexts.update(part.contexts)
            result._geometry_templates.update(part._geometry_templates)
            result._failure_banks.extend(part._failure_banks)
        return result

    def prepare(self, section, hoops, engine, *, first_diameter=None, max_layers=None,
                face="top", preserve_support_alternatives=True, compile_membership=True):
        hoops = tuple(hoops)
        key = self._key(section, hoops, engine, first_diameter=first_diameter,
                          max_layers=max_layers, face=face,
                          preserve_support_alternatives=preserve_support_alternatives)
        if key not in self.contexts:
            template_prefix = self._prefix(section, engine)
            resolver = lambda active, hoop, design, *, face: self._template_resolve(
                active, hoop, design, face=face, engine=engine, prefix=template_prefix)
            # Single-hoop geometry is independent of the leg count, root
            # diameter and face; compile the legs=2, all-root superset once.
            if len(hoops) == 1 and section.clear_cover_to_stirrup_outer_mm is not None:
                base_key = (section, int(hoops[0].diameter_mm), _policy_fingerprint(engine), max_layers)
                if self._last_base_context is None or self._last_base_context[0] != base_key:
                    base = build_context(section, (replace(hoops[0], legs=2),), engine,
                        first_diameter=None, max_layers=max_layers, face="top",
                        preserve_support_alternatives=True, geometry_resolver=resolver)
                    self._last_base_context = (base_key, base)
                base = self._last_base_context[1]
                context = dict(base)
                context["key"] = key
                context["minimum_count"] = max(2, int(hoops[0].legs))
                if first_diameter is not None:
                    context["first"] = (first_diameter,)
                self.contexts[key] = context
            else:
                self.contexts[key] = build_context(section, hoops, engine,
                    first_diameter=first_diameter, max_layers=max_layers, face=face,
                    preserve_support_alternatives=preserve_support_alternatives,
                    geometry_resolver=resolver)
                context = self.contexts[key]
        context = self.contexts[key]
        if compile_membership and "membership" not in context:
            context["membership"] = _compile_membership(context, engine, preserve_support_alternatives)
            context["membership_starts"] = [item[0] for item in context["membership"]]
        return key

    def prepare_geometry(self, section, hoop, engine, *, max_layers=3):
        """Prepare physical geometry for one hoop before candidate queries."""
        if section.clear_cover_to_stirrup_outer_mm is None or section.depth_mm >= 1000000.0:
            raise LayoutDictionaryError("DICT_GEOMETRY_TEMPLATE_REQUIRES_EXPLICIT_CLEAR_COVER")
        prefix = self._prefix(section, engine)
        return build_context(section, (replace(hoop, legs=2),), engine, max_layers=max_layers,
            geometry_resolver=lambda active, stirrup, design, *, face: self._template_resolve(
                active, stirrup, design, face=face, engine=engine, prefix=prefix))["key"]

    def _template_resolve(self, section, hoop, design, *, face, engine, prefix):
        # Explicit clear cover makes the cross-section slot geometry independent
        # of section height, material, profile and face. Centroid-cover sections
        # retain the complete-context resolver instead.
        if section.clear_cover_to_stirrup_outer_mm is None or section.depth_mm >= 1000000.0:
            self.query_audit["physical_layout_computations"] += 1
            return engine._resolve_physical_layout_uncached(section, hoop, design, face=face)
        rows = tuple((r.count, r.diameter_mm, r.role, r.credited_area_mm2) for r in design.rows)
        key = (prefix, int(hoop.diameter_mm), rows)
        template = self._template_value(key)
        if template is None:
            template_section = replace(section, depth_mm=1000000.0, name="{section}")
            try:
                self.query_audit["physical_layout_computations"] += 1
                template = engine._resolve_physical_layout_uncached(template_section, hoop, design, face="{face}")
            except DetailingError as error:
                template = str(error)
            self._geometry_templates[key] = template
        if isinstance(template, str):
            raise DetailingError(template.replace("{section}", section.name).replace("{face}", face))
        cover = section.clear_cover_for_layout_mm(hoop.diameter_mm, design.maximum_main_diameter_mm)
        # Reconstruct raw centers for the exact vertical and depth checks.
        centers = []
        previous = 0.0
        gap = engine.code.minimum_clear_spacing(max(r.diameter_mm for r in design.rows), section.aggregate_mm)
        for row in design.rows:
            center = cover + hoop.diameter_mm + row.diameter_mm / 2.0
            if centers:
                center = centers[-1] + previous / 2.0 + gap + row.diameter_mm / 2.0
            centers.append(center)
            previous = row.diameter_mm
        outer = centers[-1] + design.rows[-1].diameter_mm / 2.0
        available = section.depth_mm - 2.0 * (cover + hoop.diameter_mm)
        if outer > cover + hoop.diameter_mm + available + 1e-9:
            raise DetailingError(f"{section.name}/{face}: ROW_VERTICAL_CLEARANCE_FAILED "
                f"required={outer:.1f} available={cover + hoop.diameter_mm + available:.1f}")
        weighted = sum(center * row.credited_area_mm2 for center, row in zip(centers, design.rows)) / design.provided_area_mm2
        effective_depth = round(section.depth_mm - weighted, 6)
        sources = tuple(dict.fromkeys((*design.constraint_sources, *template.constraint_sources[2:])))
        # Rows, required area, and clear spacing are passed through unchanged.
        # Return the immutable input only after all physical checks, when the
        # geometry and added constraint sources are unchanged as well.
        if (design.row_centers_mm == template.row_centers_mm
                and design.row_x_mm == template.row_x_mm
                and design.effective_depth_mm == effective_depth
                and design.constraint_sources == sources):
            return design
        return replace(template, rows=design.rows, required_area_mm2=design.required_area_mm2,
            clear_spacing_mm=design.clear_spacing_mm, effective_depth_mm=effective_depth,
            constraint_sources=sources)

    def resolve_geometry(self, section, hoop, design, *, face, engine):
        self.query_audit["geometry_lookup_calls"] += 1
        scope = (getattr(engine, "_r7_span_caches", None)
                 if getattr(engine, "_r7_span_caches_enabled", False) else None)
        cache = scope.setdefault("r9_lookup_geometry", OrderedDict()) if scope is not None else None
        cache_key = (self, section, hoop, design, face, _policy_fingerprint(engine), str(engine.ranking_mode)) if cache is not None else None
        if cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                self.query_audit["geometry_span_cache_hits"] += 1
                cache.move_to_end(cache_key)
                success, value = cached
                if success:
                    return value
                self.query_audit["geometry_failures"] += 1
                error_type, error_args = value
                raise error_type(*error_args)
            self.query_audit["geometry_span_cache_misses"] += 1
        self.query_audit["geometry_actual_resolves"] += 1
        try:
            resolved = self._resolve_geometry(section, hoop, design, face=face, engine=engine)
        except LayoutDictionaryMiss:
            self.query_audit["geometry_misses"] += 1
            raise
        except DetailingError as error:
            self.query_audit["geometry_failures"] += 1
            if cache is not None:
                self._store_span_geometry(cache, cache_key, (False, (type(error), error.args)))
            raise
        if cache is not None:
            self._store_span_geometry(cache, cache_key, (True, resolved))
        return resolved

    def _store_span_geometry(self, cache, key, value):
        if len(cache) >= 4096:
            cache.popitem(last=False)
            self.query_audit["geometry_span_cache_evictions"] += 1
        cache[key] = value

    def _resolve_geometry(self, section, hoop, design, *, face, engine):
        """Return a compiled geometry hit or raise LayoutDictionaryMiss.

        Only ordinary credited physical rows are reusable; other row types
        require their own explicit resolution. Failure records preserve errors.
        """
        if any(row.role != "longitudinal" or row.credited_area_mm2 !=
               row.count * math.pi * row.diameter_mm ** 2 / 4.0 for row in design.rows):
            raise LayoutDictionaryMiss("DICT_MISS: geometry row credit differs")
        rows = tuple((row.count, row.diameter_mm) for row in design.rows)
        if section.clear_cover_to_stirrup_outer_mm is not None and section.depth_mm < 1000000.0:
            prefix = self._prefix(section, engine)
            full_rows = tuple((r.count, r.diameter_mm, r.role, r.credited_area_mm2)
                              for r in design.rows)
            if self._template_value((prefix, int(hoop.diameter_mm), full_rows)) is not None:
                self.query_audit["geometry_template_hits"] += 1
                return self._template_resolve(section, hoop, design, face=face,
                                              engine=engine, prefix=prefix)
        candidate = json.loads(context_key(section, (hoop,), engine, face=face))
        for key, context in self.contexts.items():
            saved = json.loads(key)
            if (saved["section"] != candidate["section"] or saved["engine"] != candidate["engine"]
                    or saved["candidate_controls"]["face"] != face):
                continue
            record = context.get("geometry", {}).get((int(hoop.diameter_mm), rows))
            if record is None:
                continue
            self.query_audit["geometry_record_hits"] += 1
            if "error" in record:
                raise DetailingError(record["error"].replace("{section}", section.name))
            suffix = record["constraint_sources"][2:]
            return replace(design,
                row_centers_mm=tuple(record["row_centers_mm"]),
                row_x_mm=tuple(tuple(row) for row in record["row_x_mm"]),
                effective_depth_mm=record["effective_depth_mm"],
                constraint_sources=tuple(dict.fromkeys((*design.constraint_sources, *suffix))))
        raise LayoutDictionaryMiss("DICT_MISS: geometry signature is not prepared")

    def query(self, required_area_mm2, section, hoops, engine, *, first_diameter=None,
              max_layers=None, face="top", preserve_support_alternatives=True):
        required = float(required_area_mm2)
        if not math.isfinite(required) or required < 0:
            raise LayoutDictionaryError("DICTIONARY_REQUIRED_AREA_INVALID")
        hoops = tuple(hoops)
        key = self._key(section, hoops, engine, first_diameter=first_diameter,
                          max_layers=max_layers, face=face,
                          preserve_support_alternatives=preserve_support_alternatives)
        self.query_audit["query_calls"] += 1
        cache_key = (key, required)
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            self.query_audit["query_cache_hits"] += 1
            self._query_cache.move_to_end(cache_key)
            return cached
        self.query_audit["query_cache_misses"] += 1
        context = self.contexts.get(key)
        if context is None:
            self.query_audit["context_misses"] += 1
            raise LayoutDictionaryMiss(f"DICT_MISS: complete physical context is not prepared; context={key}")
        if getattr(engine, "coordination_solver_revision_id", None) == "coordination_v3_top10_v1":
            if context.get("index_only"):
                raise LayoutDictionaryMiss("V3_RECORD_QUERY_REQUIRES_PREPARED_PHYSICAL_RECORDS")
            allowed_first = set(context.get("first", ()))
            minimum_count = int(context.get("minimum_count", 2))
            output = []
            for rows, value in context["records"].items():
                if value is None:
                    continue
                record, ratio_ok = value
                if not ratio_ok:
                    continue
                physical = tuple(PhysicalRow(int(count), int(diameter)) for count, diameter in rows)
                if not physical or physical[0].count < minimum_count:
                    continue
                if allowed_first and physical[0].diameter_mm not in allowed_first:
                    continue
                if _is_two_row_four_three(physical, engine):
                    continue
                provided = float(record["provided_area_mm2"])
                minimum = float(record["as_min_mm2"])
                if provided + 1e-9 < max(required, minimum):
                    continue
                if getattr(engine, "strict_symmetric_rows", False):
                    candidate_record = dict(record)
                    candidate_record["width_mm"] = float(section.width_mm)
                    if not _v3_record_has_mirrored_subset(candidate_record):
                        continue
                output.append(_materialize(record, required))
            self.query_audit["materialized_count"] += len(output)
            self.query_audit["sort_calls"] += 1
            output.sort(key=lambda item: _candidate_sort_key(item, section, engine))
            result = tuple(output)
            self._query_cache[cache_key] = result
            if len(self._query_cache) > 512:
                self._query_cache.popitem(last=False)
            return result
        events = context["membership"]
        stop = bisect_right(context["membership_starts"], required)
        if context.get("index_only"):
            first_hoop = hoops[0]
            output = []
            for low, high, rows, minimum in events[:stop]:
                if required > high:
                    continue
                design = LongitudinalDesign(rows=tuple(PhysicalRow(*row) for row in rows),
                    required_area_mm2=max(required, minimum),
                    constraint_sources=("FINITE_PHYSICAL_ROWS", "FIRST_ROW_HOOP_SUPPORT"))
                self.query_audit["materialized_count"] += 1
                output.append(self.resolve_geometry(section, first_hoop, design, face=face, engine=engine))
        else:
            output = [_materialize(context["records"][rows][0], required)
                      for low, high, rows in events[:stop] if required <= high]
            self.query_audit["materialized_count"] += len(output)
        self.query_audit["sort_calls"] += 1
        output.sort(key=lambda item: _candidate_sort_key(item, section, engine))
        if engine.avoid_two_row_4_3:
            unique = {}
            for item in output:
                unique.setdefault(signature(item), item)
            output = list(unique.values())
        result = tuple(output)
        self._query_cache[cache_key] = result
        if len(self._query_cache) > 512:
            self._query_cache.popitem(last=False)
        return result
