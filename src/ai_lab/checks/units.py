"""Pint-backed quantities + typed semantic dimensions.

Architecture (PR-02): semantic Dimension → reference unit → Pint dimensionality.
String unit equality is forbidden — the registry decides for real units.

Legacy SI base letters (``L``, ``L**2``, ``M``, ``T``) appear in Chief/LLM
``expected_dimensions``. They are *not* Pint unit strings here: ``L`` in Pint
means litre (volume). Contract tokens map via ``_LEGACY_SI_TO_DIMENSION`` before
any ``parse_quantity`` call. Actual declared units still go through Pint as-is
(``L`` / ``liter`` = volume). Prefer ``length`` / ``area`` / ``m`` / ``m**2`` in
new specs.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache

import pint

from ai_lab.core.models import Quantity

# Shared registry: constructing UnitRegistry is expensive and units must compare
# against the same definition table.
_UREG = pint.UnitRegistry()

# LLM often annotates units as "W (electrical)" / "1/s (Hz)" — strip the gloss.
_PAREN_GLOSS = re.compile(r"\s*\([^)]*\)\s*$")


class Dimension(str, Enum):
    """Semantic physical dimension — primary contract identity (not a Pint symbol)."""

    LENGTH = "length"
    MASS = "mass"
    TIME = "time"
    AREA = "area"
    VOLUME = "volume"
    FORCE = "force"
    ENERGY = "energy"
    POWER = "power"
    PRESSURE = "pressure"  # stress uses the same dimensionality (Pa)
    TEMPERATURE = "temperature"
    DIMENSIONLESS = "dimensionless"


# Reference SI units for Dimension → Pint dimensionality (never SI letters L/M/T).
_DIMENSION_REFERENCE_UNIT: dict[Dimension, str] = {
    Dimension.LENGTH: "m",
    Dimension.MASS: "kg",
    Dimension.TIME: "s",
    Dimension.AREA: "m**2",
    Dimension.VOLUME: "m**3",
    Dimension.FORCE: "N",
    Dimension.ENERGY: "J",
    Dimension.POWER: "W",
    Dimension.PRESSURE: "Pa",
    Dimension.TEMPERATURE: "K",
    Dimension.DIMENSIONLESS: "dimensionless",
}

# Legacy Chief/LLM SI-base letters → semantic Dimension.
# Intentionally NOT fed to Pint: Pint's ``L`` is litre ([length]**3).
_LEGACY_SI_TO_DIMENSION: dict[str, Dimension] = {
    "L": Dimension.LENGTH,
    "L**2": Dimension.AREA,
    "L^2": Dimension.AREA,
    "L**3": Dimension.VOLUME,
    "L^3": Dimension.VOLUME,
    "M": Dimension.MASS,
    "T": Dimension.TIME,
}


class UnitError(ValueError):
    """Unit string cannot be parsed by the registry."""


class IncompatibleDimensionsError(ValueError):
    """Two quantities do not share a dimensionality (e.g. Pa vs N)."""


class DimensionError(ValueError):
    """Expected dimension token is unknown or conflicts with Pint unit identity."""


@lru_cache(maxsize=1)
def unit_registry() -> pint.UnitRegistry:
    return _UREG


def normalize_unit_tag(unit: str) -> str:
    """Strip trailing parenthetical glosses; keep the measurable unit token."""
    return _PAREN_GLOSS.sub("", (unit or "").strip()).strip()


def parse_quantity(value: float, unit: str = "") -> pint.Quantity:
    """Build a Pint quantity. Empty / dimensionless units stay dimensionless.

    Does **not** reinterpret ``L`` as length — Pint litre semantics apply.
    Contract expected_dimensions must use ``resolve_dimension_token`` instead.
    """
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

    Dimension *names* like ``length`` are not units — use ``resolve_dimension_token``.
    Bare ``L`` is parseable here as litre (Pint); contract code must resolve legacy
    SI letters via ``resolve_dimension_token`` *before* treating them as units.
    """
    unit_s = normalize_unit_tag(unit)
    if unit_s in {"", "1", "dimensionless"}:
        return True
    try:
        parse_quantity(1.0, unit_s)
        return True
    except UnitError:
        return False


def resolve_dimension_token(
    token: str,
    *,
    allow_legacy_si: bool = True,
) -> Dimension | None:
    """Map a contract token to Dimension, or None if it is a plain unit / junk.

    Order: Dimension enum value → (optional) legacy SI letters → None.

    ``allow_legacy_si`` must be True only for *expected* contract tokens
    (CalculationSpec.expected_dimensions). On *actual* declared units, bare
    ``L`` is litre — never remap to length.
    """
    raw = normalize_unit_tag(token)
    if not raw:
        return Dimension.DIMENSIONLESS
    lowered = raw.lower()
    # Semantic names / enum values (length, area, pressure, …).
    for dim in Dimension:
        if lowered == dim.value:
            return dim
    # Legacy SI base letters — only on expected side (Chief emits L, L**2).
    if allow_legacy_si and raw in _LEGACY_SI_TO_DIMENSION:
        return _LEGACY_SI_TO_DIMENSION[raw]
    return None


def is_valid_expected_dimension(token: str) -> bool:
    """True if token may appear in CalculationSpec.expected_dimensions.

    Accepts Dimension names, legacy SI letters (L, L**2, …), or real Pint units.
    """
    unit_s = normalize_unit_tag(token)
    if unit_s in {"", "1", "dimensionless"}:
        return True
    if resolve_dimension_token(unit_s, allow_legacy_si=True) is not None:
        return True
    return is_parseable_unit(unit_s)


def reference_quantity(dim: Dimension) -> pint.Quantity:
    """Pint quantity whose dimensionality represents this semantic Dimension."""
    ref = _DIMENSION_REFERENCE_UNIT[dim]
    return parse_quantity(1.0, ref)


def unit_matches_dimension(unit: str, dim: Dimension) -> bool:
    """True iff ``unit`` is a real Pint unit of the given semantic Dimension."""
    unit_s = normalize_unit_tag(unit)
    if dim is Dimension.DIMENSIONLESS:
        return unit_s in {"", "1", "dimensionless"} or (
            is_parseable_unit(unit_s)
            and same_dimension(parse_quantity(1.0, unit_s), reference_quantity(dim))
        )
    if not is_parseable_unit(unit_s) or unit_s in {"", "1", "dimensionless"}:
        return False
    # Actual side: never remap litre ``L`` to length — Pint volume stays volume.
    try:
        return same_dimension(parse_quantity(1.0, unit_s), reference_quantity(dim))
    except UnitError:
        return False


def dimensions_equivalent(a: Dimension, b: Dimension) -> bool:
    """Compare semantic dimensions by Pint dimensionality (AREA ≡ length²)."""
    return reference_quantity(a).dimensionality == reference_quantity(b).dimensionality


def dimension_from_pint(pq: pint.Quantity) -> Dimension | None:
    """Best-effort match of a Pint quantity to a known Dimension enum member."""
    for dim in Dimension:
        if same_dimension(pq, reference_quantity(dim)):
            return dim
    return None


def dimension_quotient(numerator: Dimension, denominator: Dimension) -> Dimension | None:
    """Return Dimension matching numerator/denominator, or None if unknown.

    Used for algebraic checks: force/area → pressure, energy/time → power.
    """
    q = reference_quantity(numerator) / reference_quantity(denominator)
    return dimension_from_pint(q)


def units_compatible(expected_unit: str, actual_unit: str) -> bool:
    """Compare expected contract token vs actual unit by semantic/Pint dimension.

    - expected Dimension name or legacy SI (``length``, ``L``, ``L**2``) + actual
      engineering unit (``m``, ``m**2``) → dimensionality match via reference units
    - expected real unit (``W``, ``Pa``, ``liter``) + actual → Pint as usual
    - actual bare ``L`` is always litre (never legacy length remap)
    - Never treats Dimension names as aliases via string equality with ``m``
    """
    exp = normalize_unit_tag(expected_unit)
    act = normalize_unit_tag(actual_unit)

    # Both dimensionless / empty → ok.
    if exp in {"", "1", "dimensionless"} and act in {"", "1", "dimensionless"}:
        return True

    # Expected: allow legacy SI. Actual: semantic names only (L = litre in Pint).
    exp_dim = resolve_dimension_token(exp, allow_legacy_si=True)
    act_dim = resolve_dimension_token(act, allow_legacy_si=False)

    # Expected is a semantic / legacy dimension token.
    if exp_dim is not None:
        if act_dim is not None:
            return dimensions_equivalent(exp_dim, act_dim)
        return unit_matches_dimension(act, exp_dim)

    # Expected is a real unit (liter, m, W, …) — Pint only; no SI-letter remap.
    if act_dim is not None:
        # Actual side declared a dimension name — rare; match via reference.
        return unit_matches_dimension(exp, act_dim)

    if not is_parseable_unit(exp) or not is_parseable_unit(act):
        return False
    try:
        return same_dimension(parse_quantity(1.0, exp), parse_quantity(1.0, act))
    except UnitError:
        return False


def expected_dimensionality(expected_token: str) -> object | None:
    """Pint dimensionality for an expected_dimensions token, or None if invalid."""
    token = normalize_unit_tag(expected_token)
    dim = resolve_dimension_token(token, allow_legacy_si=True)
    if dim is not None:
        return reference_quantity(dim).dimensionality
    if not is_parseable_unit(token):
        return None
    try:
        return parse_quantity(1.0, token).dimensionality
    except UnitError:
        return None


def convert_magnitude(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a scalar between compatible units; raise on dimension mismatch."""
    src = parse_quantity(float(value), from_unit)
    dst = parse_quantity(1.0, to_unit)
    return float(convert_to(src, dst).magnitude)
