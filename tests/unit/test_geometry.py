from src.utils.geometry import distance, plan_angle_deg


def test_distance_is_euclidean() -> None:
    assert distance((0.0, 0.0, 0.0), (3.0, 4.0, 0.0)) == 5.0


def test_plan_angle_treats_reversed_collinear_beams_as_aligned() -> None:
    assert plan_angle_deg((0, 0, 0), (1, 0, 0), (2, 0, 0), (1, 0, 0)) == 0.0
