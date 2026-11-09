"""Refluxion - a lightweight, Aspen-style process simulator for learning and quick design studies.

    from refluxion import Flowsheet, Heater, Flash2
    fs = Flowsheet("demo", components=[("C3", "propane"), ("NC4", "n-butane")], method="PENG-ROB", units="ENG")
    fs.feed("FEED", T=100, P=200, flows={"C3": 50, "NC4": 50})
    fs.add(Flash2("F1", P=100, Q=0), inlets=["FEED"], outlets=["VAP", "LIQ"])
    fs.run()
"""

from .blocks import DSTWU, PALETTE, Compr, Dupl, Flash2, FSplit, HeatX, Heater, Mixer, Pump, RadFrac, RStoic, Sep, UtilityHX, Valve
from .components import Component, ComponentList, lookup
from .flowsheet import ConvergenceError, Flowsheet
from .units import UnitSet

__version__ = "0.1.0"

__all__ = ["Flowsheet", "ConvergenceError", "UnitSet", "Component", "ComponentList", "lookup", "PALETTE",
           "Mixer", "FSplit", "Sep", "Dupl", "Heater", "Flash2", "Pump", "Compr", "Valve", "RStoic", "HeatX",
           "RadFrac", "DSTWU", "UtilityHX"]
