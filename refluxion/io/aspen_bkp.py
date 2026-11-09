"""Read Aspen Plus backup (.bkp) files: model inputs and the results Aspen saved with them.

A ``.bkp`` is plain text. The input part is a keyword language (``BLOCK BLKID = ... IN = (...)``,
``? BLOCK RADFRAC COL ? ... PARAM NSTAGE = 30 ...``), and a converged file also carries Aspen's
results as ``DSET`` records. This reader pulls out what a Refluxion model needs and what a
benchmark test checks against:

* components in Aspen's order, block types and stream connectivity, raw block input text
* feed stream specifications
* stream results (T, P, mass flows and mass fractions), RadFrac duties, design-spec / calculator values

Units are those of the file's unit set (e.g. METCBAR: C, bar, kg/hr). Duties are
converted from Gcal/hr to kW (1 Gcal/hr = 1163 kW).

    from refluxion.io import read_bkp
    f = read_bkp("my_case.bkp")
    f.results.streams["ACN"].mass_frac["ACN"]     # 0.99964
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

GCAL_HR_TO_KW = 1163.0

_STOP = (r'(?= \? (?:"?BLOCK|"?EO-|"?DESIGN|"?CALCULATOR|"?CONV-OPT|"?REPORT|"?PROP-SOURCES|"?STREAM|'
         r'"?SENSITIVITY|"?STREAM-REPOR|"?FLOWSHEET|"?SETUP))')


@dataclass
class ComponentRef:
    id: str
    aname: str


@dataclass
class BlockRef:
    name: str
    type: str
    inlets: list
    outlets: list
    params: str = ""


@dataclass
class FeedRef:
    name: str
    T_C: float | None
    P_bar: float | None
    basis: str
    total: float | None
    flows: dict


@dataclass
class StreamResult:
    name: str
    source: str | None
    dest: str | None
    phase: str
    T_C: float
    P_bar: float
    mole_flow_kmol_h: float
    mass_flow_kg_h: float
    mass_flows: dict
    mass_frac: dict


@dataclass
class ColumnResult:
    name: str
    Qc_kW: float
    Qr_kW: float
    T_top_C: float | None = None
    T_bottom_C: float | None = None


@dataclass
class ColumnProfile:
    """Stage profiles Aspen saved for a RadFrac (stage 1 first). x, y, K are per stage, ordered as ``components``."""
    name: str
    components: list
    T_C: list
    P_bar: list
    x: list
    y: list
    K: list


@dataclass
class Results:
    streams: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)
    columns: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)     # name -> (final value, unit)


@dataclass
class BkpFile:
    path: str
    components: list
    blocks: dict
    feeds: dict
    results: Results
    text: str = field(repr=False, default="")

    @property
    def component_ids(self):
        return [c.id for c in self.components]


def _num(tok: str) -> float:
    return float(tok.replace("D", "E"))


def _unq(s: str) -> str:
    return s.strip().strip('"')


def _load(path) -> str:
    t = Path(path).read_bytes().decode("latin-1").replace(chr(92), "|")
    t = re.sub(r"\s+", " ", t)
    return re.sub(r'"SS-FLOWEMBED" SSXML = \(.*?\)', "", t)


def _components(t: str) -> list:
    m = re.search(r"DSET COMPONENTS MAIN COMPS @\w+ \(([^)]*)\)", t)
    if not m:
        return []
    toks = re.findall(r'"[^"]*"|\S+', m.group(1))
    return [ComponentRef(_unq(toks[k]), _unq(toks[k + 2])) for k in range(0, len(toks) - 4, 5)]


def _ports(group: str) -> list:
    toks = re.findall(r'"[^"]*"|\S+', group)
    return [_unq(toks[k]) for k in range(0, len(toks), 2)]


def _blocks(t: str) -> dict:
    out = {}
    for m in re.finditer(r'BLOCK BLKID = ("[^"]+"|\S+) BLKTYPE = "?(\w+)"? MDLTYPE = "?\w+"? IN = \(([^)]*)\) OUT = \(([^)]*)\)', t):
        name = _unq(m.group(1))
        if name not in out:
            out[name] = BlockRef(name, m.group(2), _ports(m.group(3)), _ports(m.group(4)))
    for m in re.finditer(r'\? BLOCK (\w+) ("[^"]+"|\S+) \? ; "\w+" ; (.*?)' + _STOP, t):
        name = _unq(m.group(2))
        if name in out and not out[name].params:
            out[name].params = m.group(3)
    return out


def _feeds(t: str, comp_ids) -> dict:
    out = {}
    for m in re.finditer(r'\? STREAM MATERIAL ("[^"]+"|[\w-]+) \? ; "\w+" ; \| SUBSTREAM (.*?)(?= \? )', t):
        body = m.group(2)
        if "TEMP =" not in body and "PRES =" not in body:
            continue
        name = _unq(m.group(1))
        get = lambda key: (lambda r: _num(r.group(1)) if r else None)(re.search(rf"\b{key} = ([-\d.ED+]+)", body))  # noqa: E731
        basis = re.search(r'BASIS = "?([\w-]+)"?', body)
        flows = {_unq(c): _num(v) for c, v in re.findall(r'CID = ("[^"]+"|[\w-]+) FLOW = ([-\d.ED+]+)', body)}
        total = get("TOTFLOW") if get("TOTFLOW") is not None else get("TOTAL")
        out[name] = FeedRef(name, get("TEMP"), get("PRES"), basis.group(1) if basis else "", total, flows)
    return out


def _results(t: str, comp_ids) -> Results:
    res = Results()
    n = len(comp_ids)
    for m in re.finditer(r'DSET STREAM MATERIAL ("[^"]+"|[\w-]+) STR_MAIN @\w+ \((.*?)\)', t):
        toks = re.sub(r"<\d+>", "", m.group(2)).split()
        src, dst, phase = toks[0], toks[1], toks[2]
        try:
            vals = [_num(x) for x in toks[4:4 * n + 9]]
        except ValueError:
            continue
        mass = vals[2 * n:3 * n]
        frac = vals[3 * n:4 * n]
        mole_tot, mass_tot, _vol, T, P = vals[4 * n:4 * n + 5]
        name = _unq(m.group(1))
        res.streams[name] = StreamResult(name, None if src == "*" else _unq(src), None if dst == "*" else _unq(dst), phase,
                                         T, P, mole_tot, mass_tot, dict(zip(comp_ids, mass)), dict(zip(comp_ids, frac)))
    for m in re.finditer(r'DSET BLOCK RADFRAC ("[^"]+"|[\w-]+) RESD @\w+ \( ?([-\d.D+]+) <18> ([-\d.D+]+) <18>', t):
        name = _unq(m.group(1))
        res.columns[name] = ColumnResult(name, _num(m.group(2)) * GCAL_HR_TO_KW, _num(m.group(3)) * GCAL_HR_TO_KW)
    for m in re.finditer(r'DSET BLOCK RADFRAC ("[^"]+"|[\w-]+) RAD_PRO2 @\w+ \(([^)]*)\)', t):
        name = _unq(m.group(1))
        if name in res.columns and res.columns[name].T_top_C is None:
            temps = [_num(x) for x in re.sub(r"<\d+>", "", m.group(2)).split()]
            res.columns[name].T_top_C, res.columns[name].T_bottom_C = temps[0], temps[-1]
    for name in res.columns:
        prof = _profile(t, name)
        if prof is not None:
            res.profiles[name] = prof
    for m in re.finditer(r'DSET (?:DESIGN-SPEC|CALCULATOR) ("[^"]+"|[\w-]+) DEFV_SUM @\w+ \(([^)]*)\)', t):
        toks = re.findall(r'"[^"]*"|\S+', m.group(2))
        nums = [x for x in toks[1:] if re.fullmatch(r"[-\d.]+D[-+]\d+", x)]
        units = [x for x in toks[1:] if x.startswith('"')]
        if len(nums) >= 2:
            res.specs[_unq(m.group(1))] = (_num(nums[1]), _unq(units[0]) if units else "")
    return res


def _floats(body: str) -> list:
    return [_num(x) for x in re.sub(r"<\d+>", "", body).split() if x != "*"]


def _profile(t: str, name: str):
    q = re.escape(name)
    lists = [_floats(m.group(1)) for m in re.finditer(rf'DSET BLOCK RADFRAC "?{q}"? RAD_PRO2 @\w+ \(([^)]*)\)', t)]
    tray = {}
    comps = None
    for key in ("TRAY_X", "TRAY_Y", "TRAYB_K"):
        m = re.search(rf'DSET BLOCK RADFRAC "?{q}"? {key} @\w+ \(([^)]*)\)', t)
        if not m:
            return None
        before = re.findall(r"IDSET \w+ \(COMPONENTS\) \(([^)]*)\)", t[:m.start()])
        comps = [_unq(c) for c in re.findall(r'"[^"]*"|\S+', before[-1])] if before else comps
        tray[key] = _floats(m.group(1))
    if len(lists) < 2 or not comps:
        return None
    n, C = len(lists[0]), len(comps)
    rows = lambda v: [v[k * C:(k + 1) * C] for k in range(n)]  # noqa: E731
    return ColumnProfile(name, comps, lists[0], lists[1], rows(tray["TRAY_X"]), rows(tray["TRAY_Y"]), rows(tray["TRAYB_K"]))


def read_bkp(path) -> BkpFile:
    """Parse an Aspen Plus ``.bkp`` file (inputs and, if converged, stored results)."""
    t = _load(path)
    comps = _components(t)
    ids = [c.id for c in comps]
    return BkpFile(str(path), comps, _blocks(t), _feeds(t, ids), _results(t, ids), t)
