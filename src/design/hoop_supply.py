"""Finite, ordered hoop-supply records for the drawing-policy selector.

The table contains catalogue supply only.  Demand, section limits and final
longitudinal geometry are deliberately applied by the caller at query time.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class HoopSupply:
    diameter_mm: int
    spacing_mm: int
    legs: int
    bar_area_mm2: float
    provided_at_over_s: float
    provided_av_over_s: float


class HoopSupplyTable:
    def __init__(self, diameters, spacings, catalog):
        self._base = {}
        self._records = {}
        # Preserve caller order; never widen the catalogue or spacing set.
        for diameter in diameters:
            area = catalog.area(diameter)
            for spacing in spacings:
                self._base[(diameter, spacing)] = (area, area / spacing)

    def get(self, diameter, spacing, legs):
        key = (diameter, spacing, legs)
        record = self._records.get(key)
        if record is None:
            area, at = self._base[(diameter, spacing)]
            # Match the existing trial's floating-point evaluation order.
            record = HoopSupply(diameter, spacing, legs, area, at, legs * area / spacing)
            self._records[key] = record
        return record
