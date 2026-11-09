"""Units of measure.

Everything inside Refluxion is SI: K, Pa, mol/s, J/mol, W (J/s). Units only matter at the
edges: when you type a number in, and when a report shows a number out. Like Aspen, a
flowsheet has a global *unit set* (ENG, SI, MET, METCBAR, ...) and any single input can
override it with an explicit ``(value, "unit")`` tuple.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- conversion factors: value_in_unit * factor = value_in_SI (temperature handled separately)

_LBMOL = 0.45359237e3  # mol per lbmol
_BTU = 1055.05585262   # J per Btu (IT)
_CAL = 4.184           # J per thermochemical cal
_HR = 3600.0
_PSI = 6894.757293168
_FT3 = 0.028316846592

FACTORS: dict[str, dict[str, float]] = {
    "pressure": {"Pa": 1.0, "N/sqm": 1.0, "kPa": 1e3, "MPa": 1e6, "bar": 1e5, "atm": 101325.0,
                 "psia": _PSI, "psi": _PSI, "mmHg": 133.322368, "torr": 133.322368, "inHg": 3386.389},
    "delta_pressure": {"Pa": 1.0, "N/sqm": 1.0, "kPa": 1e3, "bar": 1e5, "atm": 101325.0, "psi": _PSI,
                       "psia": _PSI, "mmHg": 133.322368},
    "delta_temperature": {"K": 1.0, "C": 1.0, "F": 5 / 9, "R": 5 / 9},
    "mole_flow": {"mol/s": 1.0, "mol/hr": 1 / _HR, "kmol/hr": 1e3 / _HR, "kmol/s": 1e3, "kmol/sec": 1e3,
                  "lbmol/hr": _LBMOL / _HR, "lbmol/s": _LBMOL, "mol/min": 1 / 60},
    "mass_flow": {"kg/s": 1.0, "kg/sec": 1.0, "kg/hr": 1 / _HR, "g/s": 1e-3, "lb/hr": 0.45359237 / _HR,
                  "lb/s": 0.45359237, "tonne/hr": 1e3 / _HR, "tonne/day": 1e3 / 86400},
    "duty": {"W": 1.0, "Watt": 1.0, "kW": 1e3, "MW": 1e6, "J/s": 1.0, "Btu/hr": _BTU / _HR,
             "MMBtu/hr": _BTU * 1e6 / _HR, "cal/sec": _CAL, "cal/s": _CAL, "kcal/hr": _CAL * 1e3 / _HR,
             "Gcal/hr": _CAL * 1e9 / _HR, "MMkcal/hr": _CAL * 1e9 / _HR, "hp": 745.699872, "kJ/hr": 1e3 / _HR},
    "molar_enthalpy": {"J/mol": 1.0, "kJ/kmol": 1.0, "J/kmol": 1e-3, "kJ/mol": 1e3, "cal/mol": _CAL,
                       "kcal/mol": _CAL * 1e3, "Btu/lbmol": _BTU / _LBMOL, "kcal/kmol": _CAL},
    "mass_enthalpy": {"J/kg": 1.0, "kJ/kg": 1e3, "Btu/lb": _BTU / 0.45359237, "kcal/kg": _CAL * 1e3, "cal/g": _CAL * 1e3},
    "molar_entropy": {"J/mol-K": 1.0, "kJ/kmol-K": 1.0, "J/kmol-K": 1e-3, "cal/mol-K": _CAL,
                      "Btu/lbmol-R": _BTU / _LBMOL * 1.8},
    "molar_heat_capacity": {"J/mol-K": 1.0, "kJ/kmol-K": 1.0, "cal/mol-K": _CAL, "Btu/lbmol-R": _BTU / _LBMOL * 1.8},
    "volume_flow": {"m3/s": 1.0, "cum/sec": 1.0, "m3/hr": 1 / _HR, "cum/hr": 1 / _HR, "L/min": 1e-3 / 60,
                    "cuft/hr": _FT3 / _HR, "cuft/min": _FT3 / 60, "gpm": 6.30901964e-5, "bbl/day": 0.158987295 / 86400},
    "mass_density": {"kg/m3": 1.0, "kg/cum": 1.0, "g/cc": 1e3, "lb/cuft": 16.01846337},
    "molar_volume": {"m3/mol": 1.0, "m3/kmol": 1e-3, "cum/kmol": 1e-3, "cc/mol": 1e-6, "L/mol": 1e-3,
                     "cuft/lbmol": _FT3 / _LBMOL},
    "length": {"m": 1.0, "meter": 1.0, "mm": 1e-3, "cm": 1e-2, "ft": 0.3048, "in": 0.0254},
    "area": {"m2": 1.0, "sqm": 1.0, "sqft": 0.09290304, "ft2": 0.09290304},
    "u_coefficient": {"W/m2-K": 1.0, "W/sqm-K": 1.0, "kW/m2-K": 1e3, "kJ/hr-m2-K": 1e3 / _HR, "kcal/hr-sqm-K": _CAL * 1e3 / _HR,
                      "Btu/hr-sqft-R": _BTU / _HR / 0.09290304 * 1.8},
    "cp_flow": {"W/K": 1.0, "kW/K": 1e3, "kW/C": 1e3, "MW/K": 1e6, "kJ/hr-K": 1e3 / _HR, "Btu/hr-R": _BTU / _HR * 1.8,
                "Btu/hr-F": _BTU / _HR * 1.8, "cal/sec-K": _CAL},
    "head": {"m": 1.0, "meter": 1.0, "ft": 0.3048},
    "power": {"W": 1.0, "kW": 1e3, "MW": 1e6, "hp": 745.699872, "Btu/hr": _BTU / _HR, "cal/sec": _CAL, "kJ/hr": 1e3 / _HR},
    "fraction": {"": 1.0, "frac": 1.0, "%": 0.01},
    "money_rate": {"$/hr": 1 / _HR, "$/yr": 1 / (8760 * _HR), "$/s": 1.0},
}

TEMPERATURE_UNITS = ("K", "C", "F", "R")


def temperature_to_si(value: float, unit: str) -> float:
    if unit == "K":
        return value
    if unit == "C":
        return value + 273.15
    if unit == "F":
        return (value - 32.0) * 5 / 9 + 273.15
    if unit == "R":
        return value * 5 / 9
    raise ValueError(f"unknown temperature unit {unit!r}")


def temperature_from_si(value: float, unit: str) -> float:
    if unit == "K":
        return value
    if unit == "C":
        return value - 273.15
    if unit == "F":
        return (value - 273.15) * 9 / 5 + 32.0
    if unit == "R":
        return value * 9 / 5
    raise ValueError(f"unknown temperature unit {unit!r}")


def to_si(value: float, quantity: str, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` to SI for the given ``quantity``."""
    if quantity == "temperature":
        return temperature_to_si(value, unit)
    if quantity == "pressure" and unit in ("psig", "barg"):
        atm_in_unit = 14.6959488 if unit == "psig" else 1.01325
        base = "psia" if unit == "psig" else "bar"
        return (value + atm_in_unit) * FACTORS["pressure"][base]
    try:
        return value * FACTORS[quantity][unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r} for {quantity}; known: {sorted(FACTORS.get(quantity, {}))}") from None


def from_si(value: float, quantity: str, unit: str) -> float:
    """Convert an SI ``value`` to ``unit``."""
    if quantity == "temperature":
        return temperature_from_si(value, unit)
    if quantity == "pressure" and unit in ("psig", "barg"):
        atm_in_unit = 14.6959488 if unit == "psig" else 1.01325
        base = "psia" if unit == "psig" else "bar"
        return value / FACTORS["pressure"][base] - atm_in_unit
    try:
        return value / FACTORS[quantity][unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r} for {quantity}") from None


# --- unit sets: the Aspen Plus built-ins plus a friendly SI-with-kW set

_BASE_SETS: dict[str, dict[str, str]] = {
    "ENG": dict(temperature="F", delta_temperature="F", pressure="psia", delta_pressure="psi",
                mole_flow="lbmol/hr", mass_flow="lb/hr", duty="Btu/hr", molar_enthalpy="Btu/lbmol",
                mass_enthalpy="Btu/lb", molar_entropy="Btu/lbmol-R", molar_heat_capacity="Btu/lbmol-R",
                volume_flow="cuft/hr", mass_density="lb/cuft", molar_volume="cuft/lbmol", length="ft",
                area="sqft", u_coefficient="Btu/hr-sqft-R", cp_flow="Btu/hr-R", head="ft", power="hp", money_rate="$/hr"),
    "SI": dict(temperature="K", delta_temperature="K", pressure="N/sqm", delta_pressure="N/sqm",
               mole_flow="kmol/sec", mass_flow="kg/sec", duty="Watt", molar_enthalpy="J/kmol",
               mass_enthalpy="J/kg", molar_entropy="J/kmol-K", molar_heat_capacity="kJ/kmol-K",
               volume_flow="cum/sec", mass_density="kg/cum", molar_volume="cum/kmol", length="meter",
               area="sqm", u_coefficient="W/sqm-K", cp_flow="W/K", head="meter", power="Watt", money_rate="$/hr"),
    "MET": dict(temperature="K", delta_temperature="K", pressure="atm", delta_pressure="atm",
                mole_flow="kmol/hr", mass_flow="kg/hr", duty="cal/sec", molar_enthalpy="cal/mol",
                mass_enthalpy="cal/g", molar_entropy="cal/mol-K", molar_heat_capacity="cal/mol-K",
                volume_flow="cum/hr", mass_density="g/cc", molar_volume="cc/mol", length="meter",
                area="sqm", u_coefficient="kcal/hr-sqm-K", cp_flow="cal/sec-K", head="meter", power="kW", money_rate="$/hr"),
    "METCBAR": dict(temperature="C", delta_temperature="C", pressure="bar", delta_pressure="bar",
                    mole_flow="kmol/hr", mass_flow="kg/hr", duty="Gcal/hr", molar_enthalpy="kcal/mol",
                    mass_enthalpy="kcal/kg", molar_entropy="cal/mol-K", molar_heat_capacity="cal/mol-K",
                    volume_flow="cum/hr", mass_density="kg/cum", molar_volume="cum/kmol", length="meter",
                    area="sqm", u_coefficient="kcal/hr-sqm-K", cp_flow="kW/K", head="meter", power="kW", money_rate="$/hr"),
    # AIChE design-contest style set: everything in kg, kmol, C, kJ, kPa, m, hr
    "AICHE": dict(temperature="C", delta_temperature="C", pressure="kPa", delta_pressure="kPa",
                  mole_flow="kmol/hr", mass_flow="kg/hr", duty="kJ/hr", molar_enthalpy="kJ/kmol",
                  mass_enthalpy="kJ/kg", molar_entropy="kJ/kmol-K", molar_heat_capacity="kJ/kmol-K",
                  volume_flow="m3/hr", mass_density="kg/m3", molar_volume="m3/kmol", length="m",
                  area="m2", u_coefficient="kJ/hr-m2-K", cp_flow="kJ/hr-K", head="m", power="kJ/hr", money_rate="$/hr"),
    "SI-KW": dict(temperature="C", delta_temperature="C", pressure="bar", delta_pressure="bar",
                  mole_flow="kmol/hr", mass_flow="kg/hr", duty="kW", molar_enthalpy="kJ/kmol",
                  mass_enthalpy="kJ/kg", molar_entropy="kJ/kmol-K", molar_heat_capacity="kJ/kmol-K",
                  volume_flow="m3/hr", mass_density="kg/m3", molar_volume="m3/kmol", length="m",
                  area="m2", u_coefficient="W/m2-K", cp_flow="kW/K", head="m", power="kW", money_rate="$/hr"),
}


@dataclass
class UnitSet:
    """A named set of display/input units, e.g. ``UnitSet("ENG", pressure="bar")``."""

    base: str = "ENG"
    overrides: dict[str, str] = field(default_factory=dict)

    def __init__(self, base: str = "ENG", **overrides: str):
        base = base.upper()
        if base not in _BASE_SETS:
            raise ValueError(f"unknown unit set {base!r}; choose from {sorted(_BASE_SETS)}")
        self.base = base
        self.overrides = overrides

    def unit(self, quantity: str) -> str:
        if quantity in self.overrides:
            return self.overrides[quantity]
        if quantity == "fraction":
            return ""
        return _BASE_SETS[self.base][quantity]

    def to_si(self, value, quantity: str):
        """Accepts a plain number (in this unit set) or a ``(number, "unit")`` tuple."""
        if value is None:
            return None
        if isinstance(value, tuple):
            number, unit = value
            return to_si(float(number), quantity, unit)
        return to_si(float(value), quantity, self.unit(quantity))

    def from_si(self, value, quantity: str):
        if value is None:
            return None
        return from_si(value, quantity, self.unit(quantity))

    def label(self, quantity: str) -> str:
        return self.unit(quantity)

    def __repr__(self) -> str:
        extra = ", ".join(f"{k}={v!r}" for k, v in self.overrides.items())
        return f"UnitSet({self.base!r}{', ' + extra if extra else ''})"


def as_unitset(units) -> UnitSet:
    if isinstance(units, UnitSet):
        return units
    return UnitSet(units or "ENG")
