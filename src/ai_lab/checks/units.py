"""Pint-backed quantities. String unit equality is forbidden here — the registry decides."""

from __future__ import annotations

import re
from functools import lru_cache

import pint

from ai_lab.core.models import Quantity

# Shared registry: constructing UnitRegistry is expensive and units must compare
# against the same definition table.
_UREG = pint.UnitRegistry()

# LLM often annotates units as "W (electrical)" / "1/s (Hz)" — strip the gloss.
_PAREN_GLOSS = re.compile(r"\s*\([^)]*\)\s*$")


class UnitError(ValueError):
    """Unit string cannot be parsed by the registry."""


class IncompatibleDimensionsError(ValueError):
    """Two quantities do not share a dimensionality (e.g. Pa vs N)."""


@lru_cache(maxsize=1)
def unit_registry() -> pint.UnitRegistry:
    return _UREG


def normalize_unit_tag(unit: str) -> str:
    """Strip trailing parenthetical glosses; keep the measurable unit token."""
    return _PAREN_GLOSS.sub("", (unit or "").strip()).strip()


def parse_quantity(value: float, unit: str = "") -> pint.Quantity:
    """Build a Pint quantity. Empty / dimensionless units stay dimensionless."""
    ureg = unit_registry()
    unit_s = normalize_unit_tag(unit)
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


def is_parseable_unit(unit: str) -> bool:
    """True iff unit string is empty/dimensionless or known to the Pint registry.

    Dimension *names* like ``length`` are not units — callers must not treat them
    as aliases for ``m``.
    """
    unit_s = normalize_unit_tag(unit)
    if unit_s in {"", "1", "dimensionless"}:
        return True
    try:
        parse_quantity(1.0, unit_s)
        return True
    except UnitError:
        return False


def units_compatible(expected_unit: str, actual_unit: str) -> bool:
    """Compare units by Pint dimensionality (W≡kW≡J/s; J≠W; length≠m).

    Unparseable unit tags (formulas, dimension names) are never compatible with
    a real engineering unit — string equality alone is not enough for PASS.
    """
    exp = normalize_unit_tag(expected_unit)
    act = normalize_unit_tag(actual_unit)
    # Both dimensionless / empty → ok.
    if exp in {"", "1", "dimensionless"} and act in {"", "1", "dimensionless"}:
        return True
    if not is_parseable_unit(exp) or not is_parseable_unit(act):
        return False
    try:
        return same_dimension(parse_quantity(1.0, exp), parse_quantity(1.0, act))
    except UnitError:
        return False


def convert_magnitude(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a scalar between compatible units; raise on dimension mismatch."""
    src = parse_quantity(float(value), from_unit)
    dst = parse_quantity(1.0, to_unit)
    return float(convert_to(src, dst).magnitude)
