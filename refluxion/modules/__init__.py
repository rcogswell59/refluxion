"""Topic modules. Each is self-contained and can be imported on its own:

pinch   heat integration: problem table, utility targets, composite and grand composite curves

Planned: cryogenic/refrigeration cycles, thermo-fluids (CoolProp), costing & economics, three-phase.
"""

from .pinch import PinchResult, PinchStream, pinch_analysis, streams_from_flowsheet

__all__ = ["PinchStream", "PinchResult", "pinch_analysis", "streams_from_flowsheet"]
