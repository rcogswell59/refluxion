"""Cost-correlation data with sources.

Turton, Bailie, Whiting, Shaeiwitz and Bhattacharyya, *Analysis, Synthesis and Design of Chemical Processes*,
4th ed. (2013), Appendix A (CAPCOST): Table A.1 (K1-K3), Table A.2 (pressure factors), Table A.4 (B1, B2) and
the tray quantity factor in Table A.5, read from https://ceecaengrtu.wordpress.com/wp-content/uploads/2020/06/capcost_utility_rm_.pdf
(accessed 2026-10-19). Purchased costs are for May-September 2001, CEPCI = 397.

Material factors come from Turton Figure A.18 / A.19, which is not legible in that copy; the values below are the
ones commonly tabulated from those figures and are marked VERIFY until checked against the printed book.
"""

CEPCI_BASE = 397.0
SOURCE = "Turton et al. (2013) Appendix A, CEPCI 397"

# kind: (K1, K2, K3, capacity unit, min, max, label)
CORRELATIONS = {
    "exchanger_floating_head": (4.8306, -0.8509, 0.3187, "m2", 10.0, 1000.0, "Floating-head shell-and-tube exchanger"),
    "exchanger_fixed_tube": (4.3247, -0.3030, 0.1634, "m2", 10.0, 1000.0, "Fixed-tube-sheet exchanger"),
    "exchanger_double_pipe": (3.3444, 0.2745, -0.0472, "m2", 1.0, 10.0, "Double-pipe exchanger"),
    "kettle_reboiler": (4.4646, -0.5277, 0.3955, "m2", 10.0, 100.0, "Kettle reboiler"),
    "vessel_vertical": (3.4974, 0.4485, 0.1074, "m3", 0.3, 520.0, "Vertical process vessel"),
    "vessel_horizontal": (3.5565, 0.3776, 0.0905, "m3", 0.1, 628.0, "Horizontal process vessel"),
    "tower": (3.4974, 0.4485, 0.1074, "m3", 0.3, 520.0, "Tower shell (tray or packed)"),
    "tray_sieve": (2.9949, 0.4465, 0.3961, "m2", 0.07, 12.30, "Sieve trays"),
    "packing_loose": (2.4493, 0.9744, 0.0055, "m3", 0.03, 628.0, "Loose tower packing"),
    "pump_centrifugal": (3.3892, 0.0536, 0.1538, "kW", 1.0, 300.0, "Centrifugal pump"),
    "pump_positive_displacement": (3.4771, 0.1350, 0.1438, "kW", 1.0, 100.0, "Positive-displacement pump"),
    "tank_fixed_roof": (4.8509, -0.3973, 0.1445, "m3", 90.0, 30000.0, "API fixed-roof storage tank"),
}

# kind: [(P_lo, P_hi, C1, C2, C3)] in barg (Table A.2)
_HX = [(-1.0, 5.0, 0.0, 0.0, 0.0), (5.0, 140.0, 0.03881, -0.11272, 0.08183)]
PRESSURE_FACTORS = {
    "exchanger_floating_head": _HX, "exchanger_fixed_tube": _HX, "kettle_reboiler": _HX,
    "exchanger_double_pipe": [(-1.0, 40.0, 0.0, 0.0, 0.0), (40.0, 100.0, 0.6072, -0.9120, 0.3327)],
    "pump_centrifugal": [(-1.0, 10.0, 0.0, 0.0, 0.0), (10.0, 100.0, -0.3935, 0.3957, -0.00226)],
    "pump_positive_displacement": [(-1.0, 10.0, 0.0, 0.0, 0.0), (10.0, 100.0, -0.245382, 0.259016, -0.01363)],
    "tank_fixed_roof": [(-1.0, 0.07, 0.0, 0.0, 0.0)],
}

# kind: (B1, B2) (Table A.4)
BARE_MODULE = {
    "exchanger_floating_head": (1.63, 1.66), "exchanger_fixed_tube": (1.63, 1.66), "kettle_reboiler": (1.63, 1.66),
    "exchanger_double_pipe": (1.74, 1.55),
    "vessel_vertical": (2.25, 1.82), "tower": (2.25, 1.82), "vessel_horizontal": (1.49, 1.52),
    "pump_centrifugal": (1.89, 1.35), "pump_positive_displacement": (1.89, 1.35),
    "packing_loose": (0.0, 1.0),
    "tank_fixed_roof": (0.0, 1.0),     # VERIFY: Turton gives F_BM = 1.1 for API tanks (Fig. A.19); used via B2 below
}

# Fm by kind and material (Fig. A.18; trays: F_BM from Fig. A.19). VERIFY against the printed figures.
# "SS316-F3": a flat 316SS exchanger material factor of 3 (a common simplified basis).
MATERIAL_FACTORS = {
    "exchanger_floating_head": {"CS": 1.0, "CS/SS316": 1.81, "SS316": 2.73, "SS316-F3": 3.0},
    "exchanger_fixed_tube": {"CS": 1.0, "CS/SS316": 1.81, "SS316": 2.73, "SS316-F3": 3.0},
    "exchanger_double_pipe": {"CS": 1.0, "CS/SS316": 1.81, "SS316": 2.73, "SS316-F3": 3.0},
    "kettle_reboiler": {"CS": 1.0, "CS/SS316": 1.81, "SS316": 2.73, "SS316-F3": 3.0},
    "vessel_vertical": {"CS": 1.0, "SS-clad": 1.74, "SS316": 3.11},
    "vessel_horizontal": {"CS": 1.0, "SS-clad": 1.74, "SS316": 3.11},
    "tower": {"CS": 1.0, "SS-clad": 1.74, "SS316": 3.11},
    "tray_sieve": {"CS": 1.0, "SS316": 1.83},
    "packing_loose": {"CS": 1.0, "SS316": 7.1},          # VERIFY: Fig. A.19 304 SS packing
    "pump_centrifugal": {"CS": 1.55, "cast iron": 1.0, "SS316": 2.28},
    "pump_positive_displacement": {"CS": 1.4, "cast iron": 1.0, "SS316": 2.7},
    "tank_fixed_roof": {"CS": 1.1, "SS316": 3.1},         # VERIFY
}
