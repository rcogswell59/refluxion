"""Binary-parameter retrieval (Aspen's "Methods > Parameters > Binary Interaction" step).

Sources are the open ChemSep tables shipped with the ``thermo`` library. Every retrieval returns
notes saying which pairs were found and which defaulted, so reports can show the same kind of
parameter provenance Aspen does. User-supplied values always win.
"""

from __future__ import annotations

import numpy as np

from ..units import to_si


def _pairs(ids):
    return [(i, j) for i in range(len(ids)) for j in range(i + 1, len(ids))]


def cubic_kij(comps, kind: str, user: dict | None = None):
    """kij matrix for PR/SRK. ChemSep publishes PR kij; SRK reuses them (flagged in notes)."""
    C = len(comps)
    kij = np.zeros((C, C))
    notes = []
    source = "none"
    if kind in ("PR", "SRK"):
        try:
            from thermo.interaction_parameters import IPDB

            kij = np.array(IPDB.get_ip_symmetric_matrix("ChemSep PR", comps.cas, "kij"), dtype=float)
            source = "ChemSep PR"
        except Exception as exc:  # pragma: no cover - library issue
            notes.append(f"kij retrieval failed ({exc}); all kij = 0")
    user = user or {}
    for (a, b), v in user.items():
        i, j = comps.idx(a), comps.idx(b)
        kij[i, j] = kij[j, i] = float(v)
    ids = comps.ids
    found = [(ids[i], ids[j], kij[i, j]) for i, j in _pairs(ids) if kij[i, j] != 0.0]
    missing = [(ids[i], ids[j]) for i, j in _pairs(ids) if kij[i, j] == 0.0]
    n = len(_pairs(ids))
    if source != "none":
        label = "PR kij from ChemSep" + (" (used for SRK - Aspen uses its own RKTKIJ databank)" if kind == "SRK" else "")
        notes.append(f"{label}: {len(found)} of {n} pairs nonzero; the rest are 0.")
    if user:
        notes.append(f"{len(user)} kij value(s) supplied by the user.")
    return kij, notes, {"found": found, "missing": missing}


R_CAL = 1.987  # cal/mol-K, as used with A_ji tables in cal/mol


def nrtl_from_A_alpha(A: dict, alpha: dict, R: float = R_CAL) -> dict:
    """Convert an ``A_ji`` / ``alpha_ji`` NRTL table (tau_ji = A_ji / (R T)) into Refluxion's user form.

    ``A[(j, i)]`` is the table entry in row j, column i, in cal/mol (R = 1.987 cal/mol-K).
    ``alpha[frozenset({i, j})]`` is the symmetric nonrandomness parameter. Refluxion's
    ``tau[j, i]`` is the same tau_ji, so ``b[j, i] = A_ji / R`` (K). Returns ``{(j, i): dict(b=, c=)}``
    for :class:`~refluxion.Flowsheet` ``nrtl=``.
    """
    out: dict = {}
    for (j, i), a in A.items():
        d = out.setdefault((j, i), {})
        d["b"] = float(a) / R
        key = frozenset({i, j})
        if key in alpha:
            d["c"] = float(alpha[key])
    for key, c in alpha.items():
        i, j = sorted(key)
        out.setdefault((i, j), {}).setdefault("c", float(c))
    return out


def nrtl_params(comps, user: dict | None = None, units: str = "K"):
    """NRTL a, b, c matrices. ChemSep gives b (K) and alpha; missing pairs default to ideal.

    ``user`` maps (i, j) -> dict(a=..., b=..., c=..., d=..., e=..., f=...) in Aspen's
    convention (i, j order matters for a, b; c is symmetric). ``b`` may be given as a
    (value, "R") tuple for Aspen ENG input files, where b is in Rankine.
    """
    C = len(comps)
    P = {k: np.zeros((C, C)) for k in "abdef"}
    P["c"] = np.full((C, C), 0.3)
    notes = []
    got = np.zeros((C, C), dtype=bool)
    try:
        from thermo.interaction_parameters import IPDB

        b = np.array(IPDB.get_ip_asymmetric_matrix("ChemSep NRTL", comps.cas, "bij"), dtype=float)
        alpha = np.array(IPDB.get_ip_asymmetric_matrix("ChemSep NRTL", comps.cas, "alphaij"), dtype=float)
        P["b"] = b
        has = (b != 0) | (b.T != 0)
        P["c"] = np.where(has & (alpha > 0), alpha, 0.3)
        got |= has
    except Exception as exc:  # pragma: no cover
        notes.append(f"NRTL retrieval failed ({exc})")
    user = user or {}
    for (a_id, b_id), vals in user.items():
        i, j = comps.idx(a_id), comps.idx(b_id)
        for key, v in vals.items():
            if isinstance(v, tuple):
                v = to_si(v[0], "delta_temperature", v[1]) if key == "b" else v[0]
            if key == "c":
                P["c"][i, j] = P["c"][j, i] = float(v)
            else:
                P[key][i, j] = float(v)
        got[i, j] = got[j, i] = True
    ids = comps.ids
    if user:
        pairs = sorted({tuple(sorted(k)) for k in user})
        notes.append(f"NRTL user-supplied parameters for {len(pairs)} pair(s): " + ", ".join(f"{a}/{b}" for a, b in pairs))
    missing = [(ids[i], ids[j]) for i, j in _pairs(ids) if not got[i, j]]
    n = len(_pairs(ids))
    notes.append(f"NRTL binary parameters: {n - len(missing)} of {n} pairs from ChemSep/user; "
                 f"{len(missing)} pair(s) default to ideal (tau = 0).")
    if missing:
        notes.append("Pairs with no NRTL data: " + ", ".join(f"{a}/{b}" for a, b in missing[:12]) +
                     (" ..." if len(missing) > 12 else ""))
    return P, notes
