from types import SimpleNamespace

from src.core.geometry_extractor import GeometryExtractor
from src.utils.geometry import distance, plan_angle_deg


def test_distance_is_euclidean() -> None:
    assert distance((0.0, 0.0, 0.0), (3.0, 4.0, 0.0)) == 5.0


def test_plan_angle_treats_reversed_collinear_beams_as_aligned() -> None:
    assert plan_angle_deg((0, 0, 0), (1, 0, 0), (2, 0, 0), (1, 0, 0)) == 0.0


def test_frame_label_list_maps_unique_name_to_display_label() -> None:
    frame_obj = SimpleNamespace(
        GetLabelNameList=lambda *_args: (
            0,
            2,
            ("1001", "1002"),
            ("B1", "B2"),
            ("F1", "F1"),
        )
    )

    labels = GeometryExtractor(SimpleNamespace(FrameObj=frame_obj))._frame_labels_by_name()

    assert labels == {"1001": "B1", "1002": "B2"}
