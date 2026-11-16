"""Offline drawing-style policy and exact pattern matching.

The runtime file is intentionally compact and is generated from the reviewed
candidate catalog.  This module does not read the source comparison catalog.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DRAWING_POLICY_ID = "drawing_20260924_v1"
DRAWING_V3_POLICY_ID = "drawing_v3"
DRAWING_V3_PREVIOUS_REVISION_ID = "drawing_v3_20261106_high_shear_d_over_2_min_hoop_legs_400_550_v2"
DRAWING_V3_REVISION_ID = "drawing_v3_20261106_valid_native_station_domain_high_shear_d_over_2_min_hoop_legs_400_550_v3"
DRAWING_V3_SUPPORTED_REVISION_IDS = frozenset({
    DRAWING_V3_PREVIOUS_REVISION_ID,
    DRAWING_V3_REVISION_ID,
})
DRAWING_V3_SOLVER_REVISION_ID = "coordination_v3_top10_v1"
DRAWING_POLICY_EXCEPTIONS = ("NET_SPACING_MAX_25_DB", "REMOVE_PH_OVER_8")
DRAWING_V3_HIGH_SHEAR_SPACING_EXCEPTION = "V3_HIGH_SHEAR_D_OVER_4_NOT_PROJECT_CONSTRAINT"
DRAWING_POLICY_REVISION_R2_ID = "drawing_20261025_t10_130_spacing_v1"
DRAWING_POLICY_REVISION_ID = DRAWING_POLICY_REVISION_R2_ID
DRAWING_POLICY_REVISION_R4_ID = "drawing_20261025_uniform_hoop_no_4_3_v1"
DRAWING_POLICY_REVISION_R5_ID = "drawing_20261025_three_layers_smf_rho_02_v1"
DRAWING_POLICY_REVISION_R6_ID = "drawing_20261025_exact_candidate_dedup_v1"
DRAWING_POLICY_REVISION_R7_ID = "drawing_20261025_etabs_only_independent_skin_v1"
DRAWING_POLICY_REVISION_R8_ID = "drawing_20261028_bounded_coordination_v1"
DRAWING_POLICY_REVISION_R9_ID = "drawing_20261031_lookup_v1"
DRAWING_ALLOWED_SPACINGS_MM = (100, 110, 120, 130, 150, 160, 180, 200)
DRAWING_ECONOMIC_REFERENCE_HOOP = {"diameter_mm": 10, "spacing_mm": 130}
DRAWING_POLICY_PARAMETERS = {
    "allowed_spacings_mm": list(DRAWING_ALLOWED_SPACINGS_MM),
    "smf_allowed_spacings_mm": list(DRAWING_ALLOWED_SPACINGS_MM),
    "economic_reference_hoop": dict(DRAWING_ECONOMIC_REFERENCE_HOOP),
    "ranking_order": [
        "non_hoop_supported_fields",
        "total_equivalent_steel_mm3",
        "hoop_supported_fields",
        "support_family_confidence",
        "row_count",
        "stable_signature",
    ],
}
DRAWING_POLICY_R4_PARAMETERS = {
    **DRAWING_POLICY_PARAMETERS,
    "avoid_two_row_4_3": True,
    "two_row_seven_bar_layout": "5/2_IF_HARD_CHECKS_PASS",
    "uniform_stirrup_diameter_per_span": True,
}
DRAWING_POLICY_R5_PARAMETERS = {
    **DRAWING_POLICY_R4_PARAMETERS,
    "max_main_bar_layers": 3,
    "smf_max_longitudinal_ratio": 0.02,
    "allow_up_to_three_layers": True,
}
DRAWING_POLICY_R6_PARAMETERS = {
    **DRAWING_POLICY_R5_PARAMETERS,
    "candidate_search_audit_version": "R6_CANDIDATE_SEARCH_AUDIT_V3",
    "candidate_search_algorithm": "EXACT_CANDIDATE_STATE_DEDUP_V1",
    "candidate_search_budget_limit": 5000,
    "candidate_search_budget_basis": "UNIQUE_CANDIDATE_VALIDATION_STATES",
    "economic_hoop_trial_reuse": "EXACT_INPUT_STATE_PER_SPAN",
    "last_layer_area_bound": "FIXED_REQUIRED_AREA_ONLY_ON_LAST_ALLOWED_LAYER",
    "candidate_search_optimization_components": [
        "EXACT_CANDIDATE_STATE_DEDUP_V1",
        "EXACT_ECONOMIC_HOOP_TRIAL_REUSE_V1",
        "PER_SPAN_FROZEN_SIGNATURE_MEMO_V1",
        "PER_SPAN_PHYSICAL_LAYOUT_CACHE_V1",
        "COMPILED_STYLE_LOOKUP_V1",
    ],
}
DRAWING_POLICY_R7_PARAMETERS = {
    **DRAWING_POLICY_R6_PARAMETERS,
    "transverse_reinforcement_source": "ETABS_DESIGN_RESULTS_ONLY",
    "selected_longitudinal_capacity_shear": False,
    "capacity_shear_source_verification": False,
    "side_reinforcement_policy": "INDEPENDENT_CONSTRUCTION_ONLY",
    "side_reinforcement_strength_credit": False,
    "side_reinforcement_style_search": False,
}
RUNTIME_SCHEMA = "drawing-style-runtime@1"
DEMAND_BUCKET_EDGES = (0.004, 0.008, 0.012, 0.01613)


class StylePolicyError(ValueError):
    """Raised when an offline style policy or runtime library is invalid."""


def default_runtime_rules_path() -> Path:
    return Path(__file__).with_name("policies") / f"{DRAWING_POLICY_ID}.json"


def _frame_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _demand_bucket(value: float) -> str | None:
    if value < 0 or not math.isfinite(value):
        return None
    for edge in DEMAND_BUCKET_EDGES:
        if value <= edge + 1e-12:
            return f"<= {edge:g}"
    return None


def _component_list(rows: Iterable[Any]) -> list[dict[str, int]]:
    counts: dict[int, int] = {}
    for row in rows:
        counts[row.diameter_mm] = counts.get(row.diameter_mm, 0) + row.count
    return [
        {"diameter_mm": diameter, "count": counts[diameter]}
        for diameter in sorted(counts)
    ]


def longitudinal_signature(design: Any, *, include_rows: bool) -> dict[str, Any]:
    main = tuple(row for row in design.rows if row.role != "hanger")
    hangers = tuple(row for row in design.rows if row.role == "hanger")
    signature: dict[str, Any] = {
        "main_components_by_diameter": _component_list(main),
        "primary_bar_count": sum(row.count for row in main),
        "hanger_state": "EXPLICIT_COMPONENTS" if hangers else "EXPLICIT_NONE",
        "hanger_physical_components_by_diameter": _component_list(hangers),
        "hanger_strength_credit": False,
    }
    if include_rows:
        signature["row_pattern"] = {
            "row_count": len(main),
            "unordered_main_row_components": sorted(
                (
                    {"diameter_mm": row.diameter_mm, "count": row.count}
                    for row in main
                ),
                key=lambda item: (item["diameter_mm"], item["count"]),
            ),
        }
    return signature


def face_signature(result: Any) -> dict[str, Any] | None:
    bars = result.face_rebar.bars_each_side
    if bars is None:
        return None
    return {
        "scope": "each_side",
        "bars_per_side_by_diameter": [
            {"diameter_mm": bars.diameter_mm, "count": bars.count}
        ],
    }


def stirrup_signature(stirrup: Any) -> dict[str, int]:
    return {
        "diameter_mm": stirrup.diameter_mm,
        "spacing_mm": stirrup.spacing_mm,
        "legs": stirrup.legs,
    }


def is_two_row_4_3_signature(signature: Mapping[str, Any] | None) -> bool:
    """Return true for a known two-row four/three main-bar pattern."""

    row_pattern = (signature or {}).get("row_pattern")
    if not isinstance(row_pattern, Mapping) or row_pattern.get("row_count") != 2:
        return False
    components = row_pattern.get("unordered_main_row_components")
    if not isinstance(components, list) or len(components) != 2:
        return False
    try:
        return sorted(int(component["count"]) for component in components) == [3, 4]
    except (KeyError, TypeError, ValueError):
        return False


def has_two_row_4_3_layout(rows: Iterable[Any]) -> bool:
    main_rows = tuple(row for row in rows if getattr(row, "role", "main") != "hanger")
    return len(main_rows) == 2 and sorted(int(row.count) for row in main_rows) == [3, 4]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class StyleFieldMatch:
    field: str
    mode_type: str
    rule_id: str
    family_count: int
    family_ids: tuple[str, ...]
    signature: Mapping[str, Any]


class OfflineStyleLibrary:
    """Validated matcher for the approved 412-rule offline runtime library."""

    R6_CONTEXT_CACHE_LIMIT = 128

    def __init__(self, path: str | Path | None = None, *, policy_id: str = DRAWING_POLICY_ID) -> None:
        self.path = Path(path) if path is not None else default_runtime_rules_path()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StylePolicyError(f"STYLE_RUNTIME_LIBRARY_UNAVAILABLE: {self.path}") from exc
        if payload.get("schema") != RUNTIME_SCHEMA:
            raise StylePolicyError("STYLE_RUNTIME_LIBRARY_SCHEMA_INVALID")
        source_policy_id = payload.get("policy_id")
        if source_policy_id != DRAWING_POLICY_ID or payload.get("approved") is not True:
            raise StylePolicyError("STYLE_RUNTIME_LIBRARY_NOT_APPROVED_FOR_POLICY")
        if payload.get("runtime_activation") != "OFFLINE_COMPARISON_ONLY":
            raise StylePolicyError("STYLE_RUNTIME_LIBRARY_SCOPE_INVALID")
        rules = payload.get("rules")
        if not isinstance(rules, list) or len(rules) != 412:
            raise StylePolicyError("STYLE_RUNTIME_LIBRARY_RULE_COUNT_INVALID")
        kind_counts = {"PB": 0, "SB": 0}
        for rule in rules:
            if not isinstance(rule, dict):
                raise StylePolicyError("STYLE_RUNTIME_LIBRARY_RULE_INVALID")
            kind = str(rule.get("kind", "")).upper()
            if kind not in kind_counts:
                raise StylePolicyError("STYLE_RUNTIME_LIBRARY_KIND_INVALID")
            kind_counts[kind] += 1
            families = rule.get("support_family_ids", [])
            if not isinstance(families, list) or len(set(families)) < 5:
                raise StylePolicyError("STYLE_RUNTIME_RULE_FAMILY_THRESHOLD_INVALID")
            if int(rule.get("development_family_count", 0)) < 5:
                raise StylePolicyError("STYLE_RUNTIME_RULE_FAMILY_COUNT_INVALID")
            if not rule.get("rule_id") or not isinstance(rule.get("pattern_signature"), dict):
                raise StylePolicyError("STYLE_RUNTIME_RULE_PATTERN_INVALID")
            if rule.get("automatic_candidate_eligible") is not True:
                raise StylePolicyError("STYLE_RUNTIME_RULE_NOT_ELIGIBLE")
            if rule.get("offline_comparison_enabled") is not True or rule.get("production_runtime_enabled") is not False:
                raise StylePolicyError("STYLE_RUNTIME_RULE_SCOPE_INVALID")
        if kind_counts != {"PB": 204, "SB": 208}:
            raise StylePolicyError("STYLE_RUNTIME_LIBRARY_KIND_COUNTS_INVALID")
        self.payload = payload
        # V3 reuses the frozen comparison rules as one signal inside its
        # bounded recommendation set. It does not turn the old drawing policy
        # or its runtime activation flag into V3's policy identity.
        self.policy_id = str(policy_id)
        self.source_policy_id = str(source_policy_id)
        self.rules = tuple(rules)
        self._r6_applicability_cache = OrderedDict()
        self._r6_lookup_counters = {
            "context_requests": 0,
            "context_cache_hits": 0,
            "context_compilations": 0,
            "compiled_pattern_entries": 0,
            "matched_field_calls": 0,
            "actual_signatures_computed": 0,
            "compiled_pattern_lookups": 0,
            "compiled_pattern_hits": 0,
        }

    def effective_rule_summary(self, engine: Any) -> dict[str, Any]:
        stats = (getattr(engine, "_r7_cache_statistics", None)
                 if getattr(engine, "detailing_policy_revision_id", None) in {DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R8_ID, DRAWING_POLICY_REVISION_R9_ID} else None)
        if stats is not None:
            stats["rule_summary_requests"] += 1
        frozen = getattr(engine, "_r7_fixed_rule_summary", None)
        if (
            getattr(engine, "_r7_span_caches_enabled", False)
            and engine.detailing_policy_revision_id in {DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R8_ID, DRAWING_POLICY_REVISION_R9_ID}
            and frozen is not None
            and frozen[0] is self
            and frozen[1] is self.rules
            and frozen[2] == self._rule_summary_context(engine)
        ):
            if stats is not None:
                stats["rule_summary_hits"] += 1
            # The summary contains only flat lists/dicts of scalar values.
            # Keep each audit independent from the engine's frozen metadata.
            return {
                name: value.copy() if isinstance(value, (dict, list)) else value
                for name, value in frozen[3].items()
            }
        if stats is not None:
            stats["rule_summary_computations"] += 1
        allowed = set(engine.project.allowed_spacings_mm) | set(
            engine.project.smf_allowed_spacings_mm
        )
        filter_reasons: dict[str, str] = {}
        for rule in self.rules:
            rule_id = str(rule["rule_id"])
            if getattr(engine, "independent_skin_rebar", False) and rule.get("field") == "face_rebar":
                filter_reasons[rule_id] = "SIDE_REINFORCEMENT_IS_INDEPENDENT_CONSTRUCTION"
            if (
                str(rule.get("field", "")).startswith("stirrup_")
                and int(rule.get("pattern_signature", {}).get("spacing_mm", -1)) not in allowed
            ):
                filter_reasons[rule_id] = "STIRRUP_SPACING_NOT_IN_REVISION_CATALOG"
            if (
                getattr(engine, "avoid_two_row_4_3", False)
                and rule.get("mode_type") == "longitudinal_rows_known"
                and is_two_row_4_3_signature(rule.get("pattern_signature"))
            ):
                filter_reasons[rule_id] = "TWO_ROW_4_3_LAYOUT_PROHIBITED"
        filtered = tuple(sorted(filter_reasons))
        summary = {
            "frozen_rule_count": len(self.rules),
            "eligible_rule_count": len(self.rules) - len(filtered),
            "filtered_rule_count": len(filtered),
            "filtered_rule_ids": list(filtered),
            "filter_reason": "STIRRUP_SPACING_NOT_IN_REVISION_CATALOG",
        }
        if getattr(engine, "avoid_two_row_4_3", False):
            summary["filter_reason"] = "REVISION_INELIGIBLE_RULES"
            summary["filtered_rule_reasons"] = filter_reasons
            summary["filtered_4_3_rule_count"] = sum(
                reason == "TWO_ROW_4_3_LAYOUT_PROHIBITED"
                for reason in filter_reasons.values()
            )
            summary["filtered_spacing_rule_count"] = sum(
                reason == "STIRRUP_SPACING_NOT_IN_REVISION_CATALOG"
                for reason in filter_reasons.values()
            )
        if getattr(engine, "independent_skin_rebar", False):
            summary["filtered_independent_skin_rule_count"] = sum(
                reason == "SIDE_REINFORCEMENT_IS_INDEPENDENT_CONSTRUCTION"
                for reason in filter_reasons.values()
            )
        return summary

    @staticmethod
    def _rule_summary_context(engine: Any) -> tuple[Any, ...]:
        return (
            tuple(engine.project.allowed_spacings_mm),
            tuple(engine.project.smf_allowed_spacings_mm),
            bool(getattr(engine, "avoid_two_row_4_3", False)),
            bool(getattr(engine, "independent_skin_rebar", False)),
        )

    @staticmethod
    def _longitudinal_demand(input_data: Any, field: str) -> float | None:
        regions = {region.name: region for region in input_data.regions}
        area = {
            "top_rebar_end_1": regions["left"].as_top_total_mm2,
            "top_rebar_middle": regions["middle"].as_top_total_mm2,
            "top_rebar_end_2": regions["right"].as_top_total_mm2,
            "bottom_rebar": max(region.as_bottom_total_mm2 for region in input_data.regions),
        }.get(field)
        if area is None:
            return None
        section = input_data.section
        return float(area) / (section.width_mm * section.depth_mm)

    @staticmethod
    def _stirrup_demand(input_data: Any, engine: Any, field: str) -> float:
        regions = {region.name: region for region in input_data.regions}
        selected = (
            (regions["middle"],)
            if field == "stirrup_continuous"
            else (regions["left"], regions["right"])
        )
        minimum = engine.code.minimum_transverse_area_per_spacing(input_data.section)
        return max(
            (
                max(region.transverse_required_av_over_s_mm2_per_mm, minimum)
                + 2.0 * region.at_over_s_mm2_per_mm
            )
            / minimum
            for region in selected
        )

    @staticmethod
    def _stirrup_bucket(value: float) -> str:
        return (
            "combined requirement / min Av/s <= 4"
            if value <= 4.0 + 1e-12
            else "combined requirement / min Av/s > 4"
        )

    @staticmethod
    def _rule_preference(rule: Mapping[str, Any]) -> tuple[int, bool, Any]:
        return (
            int(rule["development_family_count"]),
            rule["mode_type"] == "longitudinal_rows_known",
            rule["rule_id"],
        )

    @staticmethod
    def _uses_r6_compiled_lookup(engine: Any) -> bool:
        return bool(
            getattr(engine, "exact_candidate_state_dedup", False)
            and getattr(engine, "ranking_mode", None) == "style"
        )

    def r6_lookup_counters_snapshot(self) -> dict[str, int]:
        return dict(self._r6_lookup_counters)

    def r6_lookup_counters_since(self, snapshot: Mapping[str, int]) -> dict[str, int]:
        return {
            name: self._r6_lookup_counters[name] - int(snapshot.get(name, 0))
            for name in self._r6_lookup_counters
        }

    def _r6_applicability_context(self, input_data: Any, engine: Any) -> dict[str, Any]:
        self._r6_lookup_counters["context_requests"] += 1
        code = engine.code
        base_code = getattr(code, "base_policy", code)
        project = engine.project
        rules = self.rules
        settings = (
            getattr(engine, "detailing_policy_id", ""),
            getattr(engine, "detailing_policy_revision_id", ""),
            getattr(engine, "ranking_mode", ""),
            bool(getattr(engine, "avoid_two_row_4_3", False)),
            tuple(project.allowed_spacings_mm),
            tuple(project.smf_allowed_spacings_mm),
            str(getattr(engine, "rules_path", "") or ""),
            str(self.path),
        )
        key = (
            id(input_data),
            id(engine),
            id(code),
            id(base_code),
            id(project),
            id(rules),
            settings,
        )
        cached = self._r6_applicability_cache.get(key)
        if (
            cached is not None
            and cached["input_data"] is input_data
            and cached["engine"] is engine
            and cached["code"] is code
            and cached["base_code"] is base_code
            and cached["project"] is project
            and cached["rules"] is rules
            and cached["settings"] == settings
        ):
            self._r6_lookup_counters["context_cache_hits"] += 1
            self._r6_applicability_cache.move_to_end(key)
            return cached

        applicable = self._applicable_fields_uncached(input_data, engine)
        compiled: dict[tuple[str, str, str], dict[str, Any]] = {}
        for field, field_rules in applicable.items():
            for rule in field_rules:
                lookup_key = (
                    field,
                    str(rule["mode_type"]),
                    _canonical(rule["pattern_signature"]),
                )
                existing = compiled.get(lookup_key)
                # Keep the same maximum winner, retaining the first rule when
                # every preference component ties just as built-in max does.
                if existing is None or self._rule_preference(rule) > self._rule_preference(existing):
                    compiled[lookup_key] = rule

        entry = {
            "input_data": input_data,
            "engine": engine,
            "code": code,
            "base_code": base_code,
            "project": project,
            "rules": rules,
            "settings": settings,
            "applicable": applicable,
            "compiled_winners": compiled,
        }
        self._r6_lookup_counters["context_compilations"] += 1
        self._r6_lookup_counters["compiled_pattern_entries"] += len(compiled)
        self._r6_applicability_cache[key] = entry
        self._r6_applicability_cache.move_to_end(key)
        while len(self._r6_applicability_cache) > self.R6_CONTEXT_CACHE_LIMIT:
            self._r6_applicability_cache.popitem(last=False)
        return entry

    def applicable_fields(self, input_data: Any, engine: Any) -> dict[str, tuple[dict[str, Any], ...]]:
        if self._uses_r6_compiled_lookup(engine):
            # Preserve the original public mutability contract for the outer
            # mapping while reusing immutable tuple values in the context.
            return dict(self._r6_applicability_context(input_data, engine)["applicable"])
        return self._applicable_fields_uncached(input_data, engine)

    def _applicable_fields_uncached(
        self, input_data: Any, engine: Any
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        section = input_data.section
        kind = str(section.beam_role or ("SB" if section.is_secondary else "PB")).strip().upper()
        actual_frame_type = getattr(section, "actual_frame_type", "")
        if kind not in {"PB", "SB"} or not str(actual_frame_type or "").strip():
            return {}
        applicable: dict[str, list[dict[str, Any]]] = {}
        for rule in self.rules:
            if rule["kind"] != kind:
                continue
            if (
                getattr(engine, "avoid_two_row_4_3", False)
                and rule.get("mode_type") == "longitudinal_rows_known"
                and is_two_row_4_3_signature(rule.get("pattern_signature"))
            ):
                continue
            if not math.isclose(float(rule["section_width_mm"]), section.width_mm, abs_tol=1e-6):
                continue
            if not math.isclose(float(rule["section_depth_mm"]), section.depth_mm, abs_tol=1e-6):
                continue
            if _frame_key(rule["actual_frame_type"]) != _frame_key(actual_frame_type):
                continue
            if rule["resolved_profile"] != section.detailing_profile.value:
                continue
            field = str(rule["field"])
            if field == "face_rebar" and getattr(engine, "independent_skin_rebar", False):
                continue
            if field in {"top_rebar_end_1", "top_rebar_middle", "top_rebar_end_2", "bottom_rebar"}:
                demand = self._longitudinal_demand(input_data, field)
                bucket = _demand_bucket(demand) if demand is not None else None
                if bucket is None or bucket != rule["required_demand_level"]:
                    continue
            elif field in {"stirrup_continuous", "stirrup_near_support"}:
                signature = rule["pattern_signature"]
                allowed = set(engine.project.allowed_spacings_for(section, "middle"))
                if int(signature.get("spacing_mm", -1)) not in allowed:
                    continue
                demand_bucket = self._stirrup_demand(input_data, engine, field)
                expected = self._stirrup_bucket(demand_bucket)
                if expected != rule["required_demand_level"]:
                    continue
            elif field == "face_rebar":
                if rule["required_demand_level"] != "CONSTRUCTION/N.A.":
                    continue
            else:
                continue
            applicable.setdefault(field, []).append(rule)

        # Known-row rules describe a subpattern for the same field. Retain
        # core and row rules, then award only one vote when matching a result.
        output: dict[str, tuple[dict[str, Any], ...]] = {}
        for field, field_rules in applicable.items():
            if field.startswith("stirrup_"):
                field_rules = [rule for rule in field_rules if rule["mode_type"] == "stirrup_diameter_spacing_legs"]
            elif field == "face_rebar":
                field_rules = [rule for rule in field_rules if rule["mode_type"] == "face_each_side"]
            output[field] = tuple(sorted(field_rules, key=lambda rule: rule["rule_id"]))
        return output

    @staticmethod
    def _actual_field_signature(result: Any, field: str, mode_type: str) -> dict[str, Any] | None:
        if field in {"top_rebar_end_1", "top_rebar_middle", "top_rebar_end_2", "bottom_rebar"}:
            region_index = {
                "top_rebar_end_1": 0,
                "top_rebar_middle": 1,
                "top_rebar_end_2": 2,
                "bottom_rebar": 1,
            }[field]
            design = (
                result.regions[region_index].bottom
                if field == "bottom_rebar"
                else result.regions[region_index].top
            )
            return longitudinal_signature(design, include_rows=mode_type == "longitudinal_rows_known")
        if field == "face_rebar":
            return face_signature(result)
        if field == "stirrup_continuous":
            return stirrup_signature(result.regions[1].stirrup)
        if field == "stirrup_near_support":
            left = stirrup_signature(result.regions[0].stirrup)
            right = stirrup_signature(result.regions[2].stirrup)
            return left if left == right else None
        return None

    def matched_fields(self, input_data: Any, result: Any, engine: Any) -> tuple[StyleFieldMatch, ...]:
        if self._uses_r6_compiled_lookup(engine):
            return self._matched_fields_r6(input_data, result, engine)
        return self._matched_fields_uncached(input_data, result, engine)

    def _matched_fields_r6(
        self, input_data: Any, result: Any, engine: Any
    ) -> tuple[StyleFieldMatch, ...]:
        self._r6_lookup_counters["matched_field_calls"] += 1
        context = self._r6_applicability_context(input_data, engine)
        applicable = context["applicable"]
        compiled = context["compiled_winners"]
        matched: list[StyleFieldMatch] = []
        for field, field_rules in applicable.items():
            matches: list[dict[str, Any]] = []
            for mode_type in dict.fromkeys(str(rule["mode_type"]) for rule in field_rules):
                actual = self._actual_field_signature(result, field, mode_type)
                self._r6_lookup_counters["actual_signatures_computed"] += 1
                if actual is None:
                    continue
                self._r6_lookup_counters["compiled_pattern_lookups"] += 1
                match = compiled.get((field, mode_type, _canonical(actual)))
                if match is not None:
                    self._r6_lookup_counters["compiled_pattern_hits"] += 1
                    matches.append(match)
            if not matches:
                continue
            rule = max(matches, key=self._rule_preference)
            family_ids = tuple(sorted(str(value) for value in rule["support_family_ids"]))
            matched.append(StyleFieldMatch(
                field=field,
                mode_type=rule["mode_type"],
                rule_id=rule["rule_id"],
                family_count=int(rule["development_family_count"]),
                family_ids=family_ids,
                signature=rule["pattern_signature"],
            ))
        return tuple(sorted(matched, key=lambda item: item.field))

    def _matched_fields_uncached(
        self, input_data: Any, result: Any, engine: Any
    ) -> tuple[StyleFieldMatch, ...]:
        applicable = self._applicable_fields_uncached(input_data, engine)
        matched: list[StyleFieldMatch] = []
        for field, field_rules in applicable.items():
            matches = []
            for rule in field_rules:
                actual = self._actual_field_signature(result, field, rule["mode_type"])
                if actual is not None and _canonical(rule["pattern_signature"]) == _canonical(actual):
                    matches.append(rule)
            if not matches:
                continue
            rule = max(matches, key=lambda item: (
                int(item["development_family_count"]),
                item["mode_type"] == "longitudinal_rows_known",
                item["rule_id"],
            ))
            family_ids = tuple(sorted(str(value) for value in rule["support_family_ids"]))
            matched.append(StyleFieldMatch(
                field=field,
                mode_type=rule["mode_type"],
                rule_id=rule["rule_id"],
                family_count=int(rule["development_family_count"]),
                family_ids=family_ids,
                signature=rule["pattern_signature"],
            ))
        return tuple(sorted(matched, key=lambda item: item.field))

    def non_hoop_matches(self, input_data: Any, result: Any, engine: Any) -> tuple[StyleFieldMatch, ...]:
        return tuple(
            item for item in self.matched_fields(input_data, result, engine)
            if not item.field.startswith("stirrup_")
        )

    @staticmethod
    def stable_signature(result: Any, *, include_face: bool = True) -> str:
        signature = {
            "rows": [
                {
                    "region": region.name,
                    "top": [(row.count, row.diameter_mm, row.role) for row in region.top.rows],
                    "bottom": [(row.count, row.diameter_mm, row.role) for row in region.bottom.rows],
                    "stirrup": stirrup_signature(region.stirrup),
                }
                for region in result.regions
            ],
        }
        if include_face:
            signature["face"] = result.face_rebar.notation
        return _canonical(signature)

    def rank_key(self, input_data: Any, result: Any, engine: Any) -> tuple[Any, ...]:
        from src.design.equivalent_steel import equivalent_steel_mm3

        matches = self.matched_fields(input_data, result, engine)
        non_hoop = tuple(item for item in matches if not item.field.startswith("stirrup_"))
        hoop = tuple(item for item in matches if item.field.startswith("stirrup_"))
        metric = equivalent_steel_mm3(result)
        if not metric.available or metric.total_mm3 is None:
            raise StylePolicyError(f"STYLE_EQUIVALENT_STEEL_UNAVAILABLE: {metric.reason}")
        row_count = sum(
            len(region.top.rows) + len(region.bottom.rows)
            for region in result.regions
        )
        return (
            -len(non_hoop),
            float(metric.total_mm3),
            -len(hoop),
            -sum(item.family_count for item in matches),
            row_count,
            self.stable_signature(result, include_face=not getattr(engine, "independent_skin_rebar", False)),
        )

    def audit(
        self,
        input_data: Any,
        result: Any,
        engine: Any,
        *,
        fallback_reason: str | None = None,
        unmatched_applicable_pattern_rule_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        applicable = self.applicable_fields(input_data, engine)
        matched = self.matched_fields(input_data, result, engine)
        return {
            "strategy": "DRAWING_STYLE_SUPPORTED_FIELDS_THEN_EQUIVALENT_STEEL",
            "policy_id": self.policy_id,
            "policy_revision_id": engine.detailing_policy_revision_id,
            "policy_parameters": engine.detailing_policy_parameters,
            "ranking_mode": "style",
            "runtime_rule_filter": self.effective_rule_summary(engine),
            "applicable_fields": sorted(applicable),
            "matched_fields": [
                {
                    "field": item.field,
                    "mode_type": item.mode_type,
                    "rule_id": item.rule_id,
                    "development_family_count": item.family_count,
                    "support_family_ids": list(item.family_ids),
                }
                for item in matched
            ],
            "fallback_reason": fallback_reason,
            "unmatched_applicable_pattern_rule_ids": sorted(set(unmatched_applicable_pattern_rule_ids)),
        }


class DrawingPolicyCodeAdapter:
    """Apply only the two approved selection exceptions over an ACI policy."""

    edition = "ACI 318-14"

    def __init__(self, base_policy: Any, *, policy_id: str = DRAWING_POLICY_ID) -> None:
        self.base_policy = base_policy
        self.policy_id = str(policy_id)
        self.edition = base_policy.edition
        self.version = f"{base_policy.version}+{self.policy_id}"
        self.spacing_rule_id = "MIN_CLEAR_MAX_25MM_OR_DB"

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_policy, name)

    @staticmethod
    def minimum_clear_spacing(diameter_mm: float, aggregate_mm: float) -> float:
        del aggregate_mm
        return max(25.0, float(diameter_mm))

    def minimum_legs(self, section: Any, region: str) -> int:
        minimum = int(self.base_policy.minimum_legs(section, region))
        if self.policy_id != DRAWING_V3_POLICY_ID:
            return minimum
        width = float(section.width_mm)
        project_minimum = 2 if width < 400.0 else 4 if width < 550.0 else 6
        return max(minimum, project_minimum)

    def maximum_stirrup_spacing(self, *args: Any, **kwargs: Any) -> tuple[float, str, tuple[tuple[str, float], ...], bool]:
        maximum, _reason, limits, high_shear = self.base_policy.maximum_stirrup_spacing(*args, **kwargs)
        adjusted: list[tuple[str, float]] = []
        for name, value in limits:
            if name == "TORSION_PH_OVER_8":
                continue
            if self.policy_id == DRAWING_V3_POLICY_ID and name == "SHEAR_HIGH_D_OVER_4":
                # The v3 project excludes this commented high-shear d/4 note;
                # high-shear diagnosis and all other spacing limits remain active.
                adjusted.append(("SHEAR_HIGH_D_OVER_2", 2.0 * value))
            else:
                adjusted.append((name, value))
        filtered = tuple(adjusted)
        if not filtered:
            return maximum, "", filtered, high_shear
        reason, maximum = min(filtered, key=lambda item: item[1])
        return maximum, reason, filtered, high_shear
