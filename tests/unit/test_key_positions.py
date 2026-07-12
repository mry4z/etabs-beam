from src.core.geometry_extractor import BeamGeometry
from src.design.key_positions import key_positions
from src.design.provider import BeamDesignResult, DesignStation


def _station(location: float, top: float, bottom: float, shear: float) -> DesignStation:
    return DesignStation(location, "T", top, "B", bottom, "V", shear, "TL", 0, "TT", 0)


def test_key_positions_use_two_depth_end_zones_and_midspan_maximum() -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (10000, 0, 0), "R", 1000)
    design = BeamDesignResult(
        "B1",
        (
            _station(500, 10, 20, 30),
            _station(1800, 40, 50, 60),
            _station(5000, 70, 80, 90),
            _station(8200, 100, 110, 120),
            _station(9500, 130, 140, 150),
        ),
    )
    result = key_positions(beam, design)
    assert result is not None
    assert result.end_zone == 2000
    assert result.left_top_flexural.area == 40
    assert result.middle_bottom_flexural.area == 80
    assert result.right_shear.area == 150


def test_key_positions_returns_none_without_a_supported_depth() -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (10, 0, 0), "Unknown")
    assert key_positions(beam, BeamDesignResult("B1", (_station(5, 1, 1, 1),))) is None


def test_key_positions_falls_back_to_nearest_station_for_empty_end_zone() -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (10000, 0, 0), "R", 1000)
    result = key_positions(beam, BeamDesignResult("B1", (_station(5000, 7, 8, 9),)))
    assert result is not None
    assert result.left_top_flexural.location == 5000
    assert result.right_shear.location == 5000
