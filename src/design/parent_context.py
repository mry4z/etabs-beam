from dataclasses import dataclass


@dataclass(frozen=True)
class ParentSpanContext:
    """Parent coordinates for one continuous section run, in millimetres."""

    parent_span_id: str
    parent_length_mm: float
    segment_start_mm: float
    left_depth_mm: float
    right_depth_mm: float

    def __post_init__(self):
        if not str(self.parent_span_id).strip() or min(self.parent_length_mm, self.left_depth_mm, self.right_depth_mm) <= 0 or self.segment_start_mm < 0:
            raise ValueError("Invalid parent span context")
