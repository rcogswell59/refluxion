"""Utilities and operating cost.

Aspen assigns a *utility* (steam, cooling water, refrigerant, electricity) to each heat duty or work stream and
prices it. A :class:`Utility` here carries what a design needs: supply and return temperatures, a price in the
units it is bought in ($/kg of steam, $/L of cooling water, $/kJ of coolant), and the heat it carries per kg
(latent heat or cp x temperature change). From that, :func:`assess` gives the utility flow and cost for a
duty, and :func:`pick_utility` the cheapest utility that can serve a temperature range with a minimum approach.

The ``DEFAULTS`` prices are placeholders for learning. Put a course's or company's utilities in the problem
folder (for example ``myproject/data.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

GJ = 1.0e9


@dataclass
class Utility:
    name: str
    kind: str                       # "heating", "cooling" or "electricity"
    price: float | None = None      # $ per GJ delivered or removed (or derived from unit_price)
    T_supply: float | None = None   # K (legacy; T_in is used when given)
    note: str = ""
    T_in: float | None = None       # K, utility supply temperature
    T_out: float | None = None      # K, utility return temperature (= T_in for condensing steam)
    unit_price: float | None = None  # $ per unit of ``basis``
    basis: str = "GJ"               # "GJ", "J", "kg" or "L" (liquid water: 1 kg per L)
    cp: float | None = None         # J/kg-K for a sensible-heat utility
    latent: float | None = None     # J/kg for condensing steam / boiling refrigerant

    @property
    def energy_per_kg(self) -> float | None:
        """J carried per kg of utility (latent heat, or cp x |T_out - T_in|)."""
        if self.latent:
            return self.latent
        if self.cp and self.T_in is not None and self.T_out is not None and self.T_out != self.T_in:
            return self.cp * abs(self.T_out - self.T_in)
        return None

    @property
    def price_per_GJ(self) -> float:
        if self.price is not None:
            return self.price
        if self.unit_price is None:
            raise ValueError(f"{self.name}: give price ($/GJ) or unit_price + basis")
        if self.basis == "GJ":
            return self.unit_price
        if self.basis == "J":
            return self.unit_price * GJ
        e = self.energy_per_kg
        if not e:
            raise ValueError(f"{self.name}: a per-kg price needs latent heat or cp with T_in/T_out")
        return self.unit_price / e * GJ          # "kg" and "L" (water) are the same here


def assess(utility: Utility, duty_W: float, hours_per_year: float = 8000.0) -> dict:
    """Utility flow and cost for a process duty (W; + heat added to the process, - heat removed)."""
    energy = abs(duty_W)
    e = utility.energy_per_kg
    flow = energy / e * 3600.0 if e else None
    cost = energy * 3600.0 / GJ * utility.price_per_GJ
    return dict(utility=utility.name, duty_W=duty_W, flow_kg_h=flow, cost_per_hr=cost, cost_per_yr=cost * hours_per_year)


def feasible(utility: Utility, *, T_hot_in=None, T_hot_out=None, T_cold_in=None, T_cold_out=None, dTmin=10.0) -> bool:
    """Counter-current terminal check: can ``utility`` cool a hot process stream (T_hot_in -> T_hot_out) or heat a
    cold one (T_cold_in -> T_cold_out) with at least ``dTmin`` at both ends? (K)"""
    if utility.kind == "cooling":
        return (T_hot_out - utility.T_in >= dTmin - 1e-9) and (T_hot_in - utility.T_out >= dTmin - 1e-9)
    if utility.kind == "heating":
        return (utility.T_out - T_cold_in >= dTmin - 1e-9) and (utility.T_in - T_cold_out >= dTmin - 1e-9)
    return True


def pick_utility(utilities, kind: str, dTmin: float = 10.0, **temps) -> Utility | None:
    """Cheapest (per GJ) utility of ``kind`` that is feasible for the given process temperatures (K)."""
    ok = [u for u in utilities if u.kind == kind and feasible(u, dTmin=dTmin, **temps)]
    return min(ok, key=lambda u: u.price_per_GJ) if ok else None


DEFAULTS = {
    "lp_steam": Utility("LP steam (~50 psig)", "heating", 8.0, 422.0),
    "mp_steam": Utility("MP steam (~150 psig)", "heating", 10.0, 459.0),
    "hp_steam": Utility("HP steam (~600 psig)", "heating", 12.0, 527.0),
    "fired_heat": Utility("Fired heater (natural gas)", "heating", 6.0),
    "cooling_water": Utility("Cooling water (90-120 F)", "cooling", 0.4, 305.0),
    "refrigerant": Utility("Refrigerant (about -20 F)", "cooling", 8.0, 244.0),
    "electricity": Utility("Electricity", "electricity", 22.0, note="about $0.08 per kWh"),
}


def operating_cost(fs, assignments: dict, hours_per_year: float = 8000.0, utilities: dict | None = None) -> dict:
    """Cost the duties/work in a solved flowsheet.

    ``assignments = {("COL1", "reboiler_duty"): "mp_steam", ("COL1", "heater_2"): "cooling_water", ...}``
    The sign of a duty decides nothing on its own: you choose the utility. Heating utilities are
    charged on positive duties, cooling utilities on the magnitude of negative duties (a positive
    duty assigned to a cooling utility is reported as a note).

    Returns dict(rows=[...], total_per_hour=$, total_per_year=$).
    """
    util = dict(DEFAULTS)
    util.update(utilities or {})
    rows, total = [], 0.0
    for (block, key), uname in assignments.items():
        u = util[uname] if isinstance(uname, str) else uname
        value = fs[block].results[key][1]  # W (duty or power)
        note = ""
        if u.kind == "heating":
            energy = max(value, 0.0)
            if value < 0:
                note = "duty is negative (heat removed) - no heating utility used"
        elif u.kind == "cooling":
            energy = max(-value, 0.0)
            if value > 0:
                note = "duty is positive (heat added) - no cooling utility used"
        else:
            energy = abs(value)
        cost_hr = energy * 3600.0 / GJ * u.price_per_GJ
        total += cost_hr
        rows.append(dict(block=block, item=key, utility=u.name, price=u.price_per_GJ, duty_W=value,
                         cost_per_hour=cost_hr, cost_per_year=cost_hr * hours_per_year, note=note))
    return dict(rows=rows, total_per_hour=total, total_per_year=total * hours_per_year, hours_per_year=hours_per_year)
