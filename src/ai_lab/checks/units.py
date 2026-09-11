"""Pint-backed quantities. String unit equality is forbidden here — the registry decides."""

from __future__ import annotations

from functools import lru_cache

import pint

from ai_lab.core.models import Quantity

# Shared registry: constructing UnitRegistry is expensive and units must compare
# against the same definition table.
_UREG = pint.UnitRegistry()


class UnitError(ValueError):
    """Unit string cannot be parsed by the registry."""


class IncompatibleDimensionsError(ValueError):
    """Two quantities do not share a dimensionality (e.g. Pa vs N)."""


@lru_cache(maxsize=1)
def unit_registry() -> pint.UnitRegistry:
    return _UREG


def parse_quantity(value: float, unit: str = "") -> pint.Quantity:
    """Build a Pint quantity. Empty / dimensionless units stay dimensionless."""
    ureg = unit_registry()
    unit_s = (unit or "").strip()
    if unit_s in {"", "1", "dimensionless"}:
        return ureg.Quantity(value, "dimensionless")
    try:
        return ureg.Quantity(value, unit_s)
    except Exception as exc:
        raise UnitError(f"Invalid unit {unit_s!r}: {exc}") from exc


def to_pint(q: Quantity) -> pint.Quantity:
    return parse_quantity(q.value, q.unit)


def from_pint(pq: pint.Quantity) -> Quantity:
    """Serialize a Pint quantity back to our value+unit model."""
    ureg = unit_registry()
    if pq.dimensionality == ureg.dimensionless.dimensionality:
        return Quantity(value=float(pq.magnitude), unit="")
    return Quantity(value=float(pq.magnitude), unit=f"{pq.units:~}")


def to_si(pq: pint.Quantity) -> Quantity:
    """Normalize to SI base units for provenance (reproducible dump)."""
    return from_pint(pq.to_base_units())


def same_dimension(a: pint.Quantity, b: pint.Quantity) -> bool:
    return a.dimensionality == b.dimensionality


def convert_to(value: pint.Quantity, target: pint.Quantity) -> pint.Quantity:
    """Convert value into target.units, or raise IncompatibleDimensionsError."""
    if not same_dimension(value, target):
        raise IncompatibleDimensionsError(
            f"Incompatible dimensions: {value.units} vs {target.units}"
        )
    try:
        return value.to(target.units)
    except pint.DimensionalityError as exc:
        raise IncompatibleDimensionsError(str(exc)) from exc
