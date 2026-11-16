"""Design-result adapters and code/project detailing engines."""

from src.design.bar_layout_capacity import (
    ACI318_14_SINGLE_LAYER_MAX_BARS,
    calculate_single_layer_max_bars,
    minimum_clear_bar_spacing_mm,
    single_layer_max_bars,
)

from src.design.detailing import (
    ACI318_14_DetailingPolicy,
    BarCatalog,
    BarSpec,
    BeamDetailingProfile,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingResult,
    BeamRegionDemand,
    BeamSection,
    ContinuityOption,
    FaceRebarMode,
    ProjectDetailingPolicy,
    ReinforcementMode,
    coordinate_adjacent_spans,
    coordinate_intraspan_result,
    make_detailing_engine,
    make_fresh_detailing_engine,
    resolve_detailing_profile,
)
from src.design.offline_style import (
    DRAWING_V3_POLICY_ID,
    DRAWING_V3_REVISION_ID,
    DRAWING_V3_SOLVER_REVISION_ID,
)
from src.design.v3 import V3LineRecommendationSet, design_line_v3

__all__ = [
    "ACI318_14_SINGLE_LAYER_MAX_BARS",
    "ACI318_14_DetailingPolicy",
    "BarCatalog",
    "BarSpec",
    "BeamDetailingProfile",
    "BeamDetailingEngine",
    "BeamDetailingInput",
    "BeamDetailingResult",
    "BeamRegionDemand",
    "BeamSection",
    "ContinuityOption",
    "FaceRebarMode",
    "ProjectDetailingPolicy",
    "ReinforcementMode",
    "calculate_single_layer_max_bars",
    "coordinate_adjacent_spans",
    "coordinate_intraspan_result",
    "design_line_v3",
    "DRAWING_V3_POLICY_ID",
    "DRAWING_V3_REVISION_ID",
    "DRAWING_V3_SOLVER_REVISION_ID",
    "make_detailing_engine",
    "make_fresh_detailing_engine",
    "V3LineRecommendationSet",
    "minimum_clear_bar_spacing_mm",
    "single_layer_max_bars",
    "resolve_detailing_profile",
]
