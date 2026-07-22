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
)

__all__ = [
    "ACI318_14_SINGLE_LAYER_MAX_BARS",
    "ACI318_14_DetailingPolicy",
    "BarCatalog",
    "BarSpec",
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
    "minimum_clear_bar_spacing_mm",
    "single_layer_max_bars",
]
