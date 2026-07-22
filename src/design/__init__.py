"""Design-result adapters and code/project detailing engines."""

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
    "coordinate_adjacent_spans",
]
