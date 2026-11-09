"""Automatic, verified flowsheet drawings for Aspen Plus .bkp files.

Aspen silently re-routes any stream whose stored route it dislikes, re-places stream labels it cannot use, and crashes on
stream objects with empty routes. So nothing here is guessed:

1. ``calibration_text`` gives every stream a crude route; the Aspen GUI re-routes them onto the true ports and saves
   (``aspen_gui.roundtrip``); ``read_ports`` reads each stream end's port offset and side back from that file.
2. ``Layout`` places blocks (fixed positions, hints, or an automatic search next to connected blocks), routes every
   stream with an orthogonal maze router (no overlaps or touching, nothing through blocks/labels/arrows, penalties for
   crossings and bends), sizes feed/product stubs and ID-box gaps from the label widths, and writes the drawing.
3. ``check`` audits a drawing *as Aspen saved it*: crossings, overlaps, lines through blocks, labels on lines/arrows/
   labels/blocks, ID-box gaps (line visible on both sides), back-tracking, and routes Aspen replaced.

Units are Aspen drawing units. Facts measured in Aspen Plus V15 (2026-11-04):
- text does NOT scale with zoom: a stream ID box is ``13.7 * len(name) + 22`` px wide and 36 px high, a block name
  about ``13.9 * len(name)`` px, at any zoom; lines and icons scale (zoom 1.0 = 55.2 px per drawing unit). So label room
  is planned for the zoom the file is saved with (``set_zoom``), and ``check`` reads that zoom back from the file;
- feed/product white arrow: about 0.2 long, drawn from the stream's ``At`` point toward its port;
- Aspen keeps a feed/product label only if it lies on the stream's own line (otherwise it re-places it);
- multi-stream ports (RadFrac feed side, mixer inlet) store each stream's place as ``$ D dx dy`` after the route
  (offset along the port's own side from the default point) followed by ``$ C 0.0``.
"""

from __future__ import annotations

import bisect
import heapq
import math
import re
from dataclasses import dataclass, field

APPROACH = 0.2          # straight run into/out of every port (Aspen draws the same)
ARROW = 0.40            # requested gap from a feed/product arrow point to its ID box (Aspen pulls it 0.1 back)
VIS = 0.25              # visible line between an ID box and the next bend or port
PX_PER_UNIT = 55.2       # screen pixels per drawing unit at zoom 1.0
ZOOM = 1.6               # zoom the drawing is planned for and saved with (set_zoom)
GRID = 0.1
CROSS_COST = 40.0
BEND_COST = 0.6
NEAR = 0.09             # parallel lines closer than this read as one line
CLEAR = 0.12            # clear space kept around arrows and block names
EPS = 1e-6

SIDE = {"r": (1, 0), "l": (-1, 0), "u": (0, 1), "d": (0, -1)}
OPP = {"r": "l", "l": "r", "u": "d", "d": "u"}


def set_zoom(z: float) -> None:
    global ZOOM
    ZOOM = z


def _u(px: float) -> float:
    """Screen pixels at the planned zoom -> drawing units."""
    return px / (PX_PER_UNIT * ZOOM)


def label_w(name: str) -> float:
    return _u(13.7 * len(name) + 22)


def box_h() -> float:
    return _u(36)


def stub_len(name: str, side: str) -> float:
    """Feed/product stub long enough for the arrow, a clear gap, the ID box and visible line."""
    if side in "lr":
        return ARROW + label_w(name) + VIS
    # vertical stubs also leave a slot beside the block for its name, so the ID box sits above/below it
    return ARROW + box_h() + VIS + _u(28) + CLEAR


# --------------------------------------------------------------------------------------------- drawing text model
_OBJ = re.compile(r"(?m)^(?=(?:BLOCK|STREAM|LEGEND)\n)")


@dataclass
class DBlock:
    name: str
    raw: str
    x: float
    y: float
    icon: str
    scale: float
    mod: int
    lx: float
    ly: float


@dataclass
class Drawing:
    text: str
    head: int                    # index of "PFSVData"
    end: int                     # index of the main flowsheet's closing "# of PFS Objects"
    objects: list                # raw object texts, in order
    blocks: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)   # sid -> raw object text

    @classmethod
    def parse(cls, text: str) -> "Drawing":
        text = text.replace("\r\n", "\n")
        m = re.search(r"PFSVData\n# of PFS Objects = \d+\n(SIZE [^\n]*\n)?", text)
        end = text.index("# of PFS Objects", m.end())
        objs = [o for o in _OBJ.split(text[m.end():end]) if o.strip()]
        d = cls(text, m.start(), end, objs)
        for o in objs:
            mid = re.search(r"(?m)^ID: (\S+)", o)
            if not mid:
                continue
            oid = mid.group(1)
            if o.startswith("BLOCK"):
                x, y = map(float, re.search(r"(?m)^At (\S+) (\S+)", o).groups())
                lab = re.search(r"(?m)^Label At (\S+) (\S+)", o)
                sc = re.search(r"Scale (\S+) Modifier (\d+)", o)
                icon = re.search(r'ICON: "([^"]*)"', o)
                lx, ly = map(float, lab.groups()) if lab else (0.0, 0.3)
                d.blocks[oid] = DBlock(oid, o, x, y, icon.group(1) if icon else "", float(sc.group(1)) if sc else 1.0,
                                       int(sc.group(2)) if sc else 0, lx, ly)
            elif o.startswith("STREAM"):
                d.streams[oid] = o
        return d

    def render(self, blocks: dict, new_streams: dict, size=None, viewport=None) -> str:
        """Rewrite blocks (``{name: (x, y, mod, (lx, ly))}``) and replace/add stream objects."""
        out = []
        for o in self.objects:
            mid = re.search(r"(?m)^ID: (\S+)", o)
            oid = mid.group(1) if mid else None
            if o.startswith("STREAM") and oid in new_streams:
                continue
            if o.startswith("BLOCK") and oid in blocks:
                x, y, mod, (lx, ly) = blocks[oid]
                o = re.sub(r"(?m)^At \S+ \S+", f"At {x:.6f} {y:.6f}", o, count=1)
                o = re.sub(r"(?m)^Label At \S+ \S+", f"Label At {lx:.6f} {ly:.6f}", o, count=1)
                o = re.sub(r"Modifier \d+", f"Modifier {mod}", o, count=1)
            out.append(o)
        out += list(new_streams.values())
        sz = "SIZE {:.4f} {:.4f} {:.4f} {:.4f}\n".format(*size) if size else ""
        text = self.text[:self.head] + f"PFSVData\n# of PFS Objects = {len(out)}\n{sz}" + "".join(out) + self.text[self.end:]
        if viewport:
            text = re.sub(r"(?m)^VIEWPORT [^\n]*$", "VIEWPORT {:.4f} {:.4f} {:.4f} {:.4f}".format(*viewport), text, count=1)
        return text


def flowsheet_zoom(text: str):
    """Zoom the Aspen GUI restores for the main flowsheet pane, or None."""
    i = text.find('PaneName="FlowsheetScreenFactory"')
    m = re.compile(r'ScreenState Zoom="([0-9.]+)"').search(text, i) if i >= 0 else None
    return float(m.group(1)) if m and m.start() - i < 2000 else None


def activate_flowsheet(text: str) -> str:
    """Make "Main Flowsheet" the most recently used tab, so the GUI opens on it. Aspen only draws, validates and
    re-routes the flowsheet when that tab is shown; a file that opens on another form keeps whatever routes it has."""
    stamps = re.findall(r"lastActivatedTime=&amp;quot;([0-9T:.\-]+)Z&amp;quot;", text)
    if not stamps:
        return text
    newest = max(stamps)
    # one second after the newest stamp, keeping Aspen's format
    from datetime import datetime, timedelta
    t = datetime.strptime(newest[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(seconds=1)
    stamp = t.strftime("%Y-%m-%dT%H:%M:%S") + ".0000000"
    return re.sub(r"(contentPane name=&amp;quot;FlowsheetScreenFactory&amp;quot; location=&amp;quot;Document&amp;quot; "
                  r"lastActivatedTime=&amp;quot;)[0-9T:.\-]+(Z&amp;quot;)", lambda m: m.group(1) + stamp + m.group(2), text,
                  count=1)


def set_flowsheet_zoom(text: str, zoom: float) -> str:
    """Set the zoom the Aspen GUI restores for the main flowsheet pane (stored in its screen-state XML)."""
    i = text.find('PaneName="FlowsheetScreenFactory"')
    if i < 0:
        return text
    m = re.compile(r'ScreenState Zoom="[0-9.]+"').search(text, i)
    if not m or m.start() - i > 2000:
        return text
    return text[:m.start()] + f'ScreenState Zoom="{zoom:.6f}"' + text[m.end():]


def stream_object(sid: str, terminal: int, at, label, route0=None, route1=None, attach0=None, attach1=None) -> str:
    """One STREAM drawing object. Each route is a point list from ``at`` to the port; its last item is ``(x, y, side)``."""

    def fmt(route, first, attach):
        if not route:
            return "$ $ 0.0 0.0\n"
        lines = [f"r {first} {route[0][0]:.6f} {route[0][1]:.6f} 0\n"]
        for a, b in zip(route[:-2], route[1:-1]):
            ax = "x" if abs(b[1] - a[1]) < 1e-9 else "y"
            lines.append(f"{ax} {ax} {b[0]:.6f} {b[1]:.6f} 0\n")
        px, py, side = route[-1]
        lines.append(f"t {side} {px:.6f} {py:.6f} 0\n")
        lines.append(f"$ D {attach[0]:.6f} {attach[1]:.6f}\n$ C 0.000000 0.0\n" if attach else "$ C 1.000000 0.0\n")
        return "".join(lines) + "$ $ 0.0 0.0\n"

    def first_dir(route):
        (x0, y0), (x1, y1) = route[0][:2], route[1][:2]
        if abs(x1 - x0) >= abs(y1 - y0):
            return "r" if x1 > x0 else "l"
        return "u" if y1 > y0 else "d"

    first = first_dir(route0 or route1) if terminal in (1, 2) else "r"
    tail = "".join(f"At 0.000000 0.000000\nLabel At 0.000000 0.000000\nROUTE 0 {k}\n$ $ 0.0 0.0\nROUTE 1 {k}\n$ $ 0.0 0.0\n"
                   for k in (1, 2))
    return (f"STREAM\nID: {sid}\nVersion: 2\nFlag 0\nGLOBAL 117\nTYPE 0 TERMINAL {terminal}\nAt {at[0]:.6f} {at[1]:.6f}\n"
            f"Label At {label[0]:.6f} {label[1]:.6f}\nROUTE 0 0\n{fmt(route0, first, attach0)}"
            f"ROUTE 1 0\n{fmt(route1, first, attach1)}{tail}")


def parse_stream(raw: str) -> dict:
    at = tuple(map(float, re.search(r"(?m)^At (\S+) (\S+)", raw).groups()))
    lab = tuple(map(float, re.search(r"(?m)^Label At (\S+) (\S+)", raw).groups()))
    term = int(re.search(r"TERMINAL (\d)", raw).group(1))
    routes = []
    for side in "01":
        r = re.search(rf"ROUTE {side} 0\n((?:[a-z$] [^\n]*\n)+?)\$ \$", raw)
        pts, end_side = [], None
        if r:
            for line in r.group(1).strip().split("\n"):
                if line.startswith("$"):
                    continue
                f = line.split()
                pts.append((float(f[2]), float(f[3])))
                if f[0] == "t":
                    end_side = f[1]
        routes.append((pts, end_side) if len(pts) > 1 else None)
    return dict(at=at, label=lab, term=term, routes=routes)


# --------------------------------------------------------------------------------------------- connectivity + ports
def connections(blocks_ref) -> dict:
    """``{sid: (source block | None, destination block | None)}`` from ``read_bkp(...).blocks``."""
    out = {}
    for b in blocks_ref.values():
        for s in b.outlets:
            out.setdefault(s, [None, None])[0] = b.name
        for s in b.inlets:
            out.setdefault(s, [None, None])[1] = b.name
    return {k: tuple(v) for k, v in out.items()}


def calibration_text(dr: Drawing, net: dict, mods: dict | None = None) -> str:
    """Connected blocks spread on a wide grid, every stream given a crude two-leg route, so the Aspen GUI re-routes each
    one onto its real port (``read_ports`` then reads them back)."""
    names = sorted({b for ends in net.values() for b in ends if b})
    pos = {}
    for i, n in enumerate(names):
        b = dr.blocks[n]
        pos[n] = (2.0 + 3.5 * (i % 6), -2.0 - 3.5 * (i // 6), (mods or {}).get(n, b.mod), (b.lx, b.ly))
    new, used = {}, set()

    def free(at):
        # two arrows on one point make Aspen merge them into a nameless object and stop re-routing the file
        while (round(at[0], 2), round(at[1], 2)) in used:
            at = (at[0] + 0.37, at[1] - 0.23)
        used.add((round(at[0], 2), round(at[1], 2)))
        return at

    for sid, (src, dst) in sorted(net.items()):
        if src and dst:
            a, b = pos[src][:2], pos[dst][:2]
            at = free(((a[0] + b[0]) / 2 + 0.31, (a[1] + b[1]) / 2 + 0.29))
            new[sid] = stream_object(sid, 0, at, (0, 0), [at, (a[0], at[1]), (a[0], a[1], "r")],
                                     [at, (b[0], at[1]), (b[0], b[1], "l")])
        elif dst:
            b = pos[dst][:2]
            at = free((b[0] - 1.3, b[1] - 1.1))
            new[sid] = stream_object(sid, 1, at, (0, 0), None, [at, (b[0], at[1]), (b[0], b[1], "d")])
        elif src:
            a = pos[src][:2]
            at = free((a[0] + 1.3, a[1] + 1.1))
            new[sid] = stream_object(sid, 2, at, (0, 0), [at, (a[0], at[1]), (a[0], a[1], "u")], None)
    return activate_flowsheet(dr.render(pos, new))


def read_ports(saved_text: str, net: dict) -> dict:
    """``{(sid, 0|1): (dx, dy, side)}`` port offsets from the block's At, as Aspen routed them (0 = source end)."""
    dr = Drawing.parse(saved_text)
    ports = {}
    for sid, ends in net.items():
        raw = dr.streams.get(sid)
        if not raw:
            continue
        st = parse_stream(raw)
        for k in (0, 1):
            blk, r = ends[k], st["routes"][k]
            if blk and r and r[1]:
                (px, py), side = r[0][-1], r[1]
                # exact (Aspen re-routes ends more than ~0.001 off); Layout.align removes the resulting micro-jogs
                ports[(sid, k)] = (round(px - dr.blocks[blk].x, 4), round(py - dr.blocks[blk].y, 4), side)
    return ports


# --------------------------------------------------------------------------------------------- geometry
def _rect_hit(a, b, pad=0.0):
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def _seg_rect(p, q, r, pad=0.0):
    """Does axis-aligned segment p-q pass through rectangle r (strict interior, grown by pad)?"""
    x0, y0, x1, y1 = r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad
    if abs(p[1] - q[1]) < 1e-9:
        lo, hi = sorted((p[0], q[0]))
        return y0 < p[1] < y1 and lo < x1 and hi > x0
    lo, hi = sorted((p[1], q[1]))
    return x0 < p[0] < x1 and lo < y1 and hi > y0


def text_rect(cx, cy, name, boxed=True):
    w = label_w(name) if boxed else _u(13.9 * len(name) + 6)
    h = box_h() if boxed else _u(28)
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def arrow_rect(at, toward):
    """Feed/product arrow: from At, 0.2 long toward the port, 0.2 wide."""
    dx, dy = toward
    x1, y1 = at[0] + 0.2 * dx, at[1] + 0.2 * dy
    wx, wy = (0.1 if abs(dy) > 0.5 else 0.0), (0.1 if abs(dx) > 0.5 else 0.0)
    return (min(at[0], x1) - wx, min(at[1], y1) - wy, max(at[0], x1) + wx, max(at[1], y1) + wy)


def block_rects(b: DBlock, x=None, y=None, ports=()):
    """Obstacle rectangles for a block icon (body plus decorations)."""
    x = b.x if x is None else x
    y = b.y if y is None else y
    s = b.scale
    if b.icon == "FRACT1":
        # measured on screen: condenser/reboiler circles at (+0.45s, +-0.83s), radius ~0.18; the top and bottom
        # product ports sit just right of them (+0.58s..+0.60s)
        return [(x - 0.27 * s, y - 0.86 * s, x + 0.27 * s, y + 0.86 * s),          # shell
                (x + 0.28 * s, y + 0.66 * s, x + 0.56 * s, y + 1.00 * s),          # condenser
                (x + 0.28 * s, y - 1.00 * s, x + 0.56 * s, y - 0.66 * s)]          # reboiler
    if b.icon == "GEN-HT":
        return [(x - 0.40, y - 0.25, x + 0.40, y + 0.25)]
    if b.icon == "V-DRUM1":
        return [(x - 0.25, y - 0.40, x + 0.25, y + 0.40)]
    if b.icon == "ICON1":
        return [(x - 0.27, y - 0.27, x + 0.27, y + 0.27)]
    xs = [x + p[0] for p in ports] or [x]          # generic icon: its ports sit on the edge
    ys = [y + p[1] for p in ports] or [y]
    return [(min(min(xs), x - 0.2), min(min(ys), y - 0.2), max(max(xs), x + 0.2), max(max(ys), y + 0.2))]


def _simplify(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) < 1e-9 and abs(p[1] - out[-1][1]) < 1e-9:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 1e-9 and abs(b[0] - p[0]) < 1e-9) or (abs(a[1] - b[1]) < 1e-9 and abs(b[1] - p[1]) < 1e-9):
                out[-1] = p
                continue
        out.append(p)
    return out


def count_crossings(paths: dict) -> int:
    segs = [(sid, p, q) for sid, pts in paths.items() for p, q in zip(pts, pts[1:])]
    n = 0
    for i, (s1, a, b) in enumerate(segs):
        for s2, c, d in segs[i + 1:]:
            if s1 != s2 and _crosses(a, b, c, d):
                n += 1
    return n


def _crosses(a, b, c, d):
    h1, h2 = abs(a[1] - b[1]) < EPS, abs(c[1] - d[1]) < EPS
    if h1 == h2:
        return False
    h, v = ((a, b), (c, d)) if h1 else ((c, d), (a, b))
    hx0, hx1 = sorted((h[0][0], h[1][0]))
    vy0, vy1 = sorted((v[0][1], v[1][1]))
    return hx0 + EPS < v[0][0] < hx1 - EPS and vy0 + EPS < h[0][1] < vy1 - EPS


# --------------------------------------------------------------------------------------------- router
class Router:
    """Orthogonal maze router on a sparse grid (uniform 0.1 lines plus every port/approach coordinate)."""

    def __init__(self, bounds, rects, keys_x, keys_y):
        x0, y0, x1, y1 = bounds
        xs = {round(x0 + i * GRID, 4) for i in range(int(round((x1 - x0) / GRID)) + 1)} | {round(v, 4) for v in keys_x}
        ys = {round(y0 + j * GRID, 4) for j in range(int(round((y1 - y0) / GRID)) + 1)} | {round(v, 4) for v in keys_y}
        self.xs = sorted(v for v in xs if x0 <= v <= x1)
        self.ys = sorted(v for v in ys if y0 <= v <= y1)
        self.ix = {v: i for i, v in enumerate(self.xs)}
        self.iy = {v: j for j, v in enumerate(self.ys)}
        self.rects = []
        self.blocked = set()
        for r in rects:
            self.add_rect(r)
        self.h = {}      # used horizontal segments, bucketed by round(y / GRID): [(y, x0, x1, sid)]
        self.v = {}      # used vertical segments, bucketed by round(x / GRID): [(x, y0, y1, sid)]

    def add_rect(self, r, pad=0.08):
        self.rects.append(r)
        i0, i1 = bisect.bisect_right(self.xs, r[0] - pad), bisect.bisect_left(self.xs, r[2] + pad)
        j0, j1 = bisect.bisect_right(self.ys, r[1] - pad), bisect.bisect_left(self.ys, r[3] + pad)
        self.blocked.update((i, j) for i in range(i0, i1) for j in range(j0, j1))

    def add_path(self, pts, sid):
        for p, q in zip(pts, pts[1:]):
            if abs(p[1] - q[1]) < 1e-9 and abs(p[0] - q[0]) > 1e-9:
                self.h.setdefault(round(p[1] / GRID), []).append((p[1], min(p[0], q[0]), max(p[0], q[0]), sid))
            elif abs(p[0] - q[0]) > 1e-9:
                raise ValueError(f"diagonal segment in {sid}: {p} -> {q}")
            elif abs(p[1] - q[1]) > 1e-9:
                self.v.setdefault(round(p[0] / GRID), []).append((p[0], min(p[1], q[1]), max(p[1], q[1]), sid))

    @staticmethod
    def _near(store, c):
        k = round(c / GRID)
        return [s for kk in (k - 1, k, k + 1) for s in store.get(kk, ())]

    @staticmethod
    def _span(store, lo, hi):
        return [s for kk in range(math.floor(lo / GRID) - 1, math.ceil(hi / GRID) + 2) for s in store.get(kk, ())]

    def segments_hit_rect(self, r, sid=None):
        return any(other != sid and _seg_rect((a, y), (b, y), r) for y, a, b, other in self._span(self.h, r[1], r[3])) or \
            any(other != sid and _seg_rect((x, a), (x, b), r) for x, a, b, other in self._span(self.v, r[0], r[2]))

    def _on_other(self, p, sid):
        x, y = p
        return any(o != sid and abs(yy - y) < NEAR and a - NEAR <= x <= b + NEAR for yy, a, b, o in self._near(self.h, y)) or \
            any(o != sid and abs(xx - x) < NEAR and a - NEAR <= y <= b + NEAR for xx, a, b, o in self._near(self.v, x))

    def _edge_cost(self, a, b, sid):
        """None if the move overlaps/touches another stream; otherwise extra cost for crossing one."""
        (xa, ya), (xb, yb) = a, b
        extra = 0.0
        horizontal = abs(ya - yb) < 1e-9
        store_par, store_perp = (self.h, self.v) if horizontal else (self.v, self.h)
        c, lo, hi = (ya, min(xa, xb), max(xa, xb)) if horizontal else (xa, min(ya, yb), max(ya, yb))
        tgt = xb if horizontal else yb
        for k, s0, s1, other in self._near(store_par, c):
            if other != sid and abs(k - c) < NEAR and s0 < hi + NEAR and s1 > lo - NEAR:
                return None
        for k, s0, s1, other in self._span(store_perp, lo, hi):
            if other == sid or not (lo - EPS <= k <= hi + EPS):
                continue
            if s0 + NEAR < c < s1 - NEAR:
                if abs(k - tgt) < EPS or lo + EPS < k < hi - EPS:
                    extra += CROSS_COST           # stepping onto (to cross) or over another line
            elif s0 - NEAR <= c <= s1 + NEAR:
                return None                       # would touch the end or a bend of another line
        return extra

    def route(self, start, start_dir, goal, goal_dir, sid, max_expand=200000):
        """A* from ``start`` (leaving in ``start_dir``) to ``goal`` (arriving while moving ``goal_dir``)."""
        s = (self.ix.get(round(start[0], 4)), self.iy.get(round(start[1], 4)))
        g = (self.ix.get(round(goal[0], 4)), self.iy.get(round(goal[1], 4)))
        if None in s or None in g:
            return None
        gx, gy = self.xs[g[0]], self.ys[g[1]]
        open_ = [(0.0, 0.0, s, start_dir, None)]
        best = {(s, start_dir): 0.0}
        parent = {}
        n = 0
        while open_ and n < max_expand:
            _, cost, node, d, par = heapq.heappop(open_)
            if (node, d) in parent:
                continue
            parent[(node, d)] = par
            n += 1
            if node == g and d == goal_dir:
                path, key = [], (node, d)
                while key:
                    path.append((self.xs[key[0][0]], self.ys[key[0][1]]))
                    key = parent[key]
                return _simplify(path[::-1])
            here = (self.xs[node[0]], self.ys[node[1]])
            for nd, (di, dj) in SIDE.items():
                if nd == OPP[d]:
                    continue
                if nd != d and node != s and self._on_other(here, sid):
                    continue                      # never bend on top of another line
                ni, nj = node[0] + di, node[1] + dj
                if not (0 <= ni < len(self.xs) and 0 <= nj < len(self.ys)):
                    continue
                nn = (ni, nj)
                if nn in self.blocked and nn != g:
                    continue
                b = (self.xs[ni], self.ys[nj])
                extra = self._edge_cost(here, b, sid)
                if extra is None:
                    continue
                c = cost + abs(b[0] - here[0]) + abs(b[1] - here[1]) + extra + (BEND_COST if nd != d else 0.0)
                if c < best.get((nn, nd), 1e18):
                    best[(nn, nd)] = c
                    heapq.heappush(open_, (c + abs(b[0] - gx) + abs(b[1] - gy), c, nn, nd, (node, d)))
        return None


# --------------------------------------------------------------------------------------------- layout
@dataclass
class Plan:
    blocks: dict          # name -> (x, y, mod, (lx, ly))
    streams: dict         # sid -> stream object text
    paths: dict           # sid -> drawn polyline
    issues: list
    crossings: int
    length: float

    @property
    def score(self) -> float:
        return 1000.0 * len(self.issues) + 100.0 * self.crossings + self.length


class Layout:
    """Place and route one flowsheet.

    ``fixed``/``hints``: ``{block: (x, y)}`` or ``(x, y, modifier)``. Fixed blocks never move; hinted blocks start
    there; the rest are placed automatically next to their connected blocks. ``keep_labels``: blocks whose label offset
    is kept as drawn (e.g. columns). ``attach``: ``{(sid, end): (dx, dy)}`` overrides for ``$ D`` records.
    """

    def __init__(self, dr: Drawing, net: dict, ports: dict, fixed=None, hints=None, keep_labels=(), attach=None):
        self.dr, self.net, self.ports = dr, net, ports
        self.fixed = dict(fixed or {})
        self.hints = dict(hints or {})
        self.keep_labels = set(keep_labels)
        self.attach_override = dict(attach or {})
        self.names = sorted({b for ends in net.values() for b in ends if b})

    def ports_of(self, name):
        return [(p[0], p[1]) for (sid, k), p in self.ports.items() if self.net[sid][k] == name]

    # ---- placement
    def footprint(self, name, x, y):
        rects = block_rects(self.dr.blocks[name], x, y, self.ports_of(name))
        for (sid, k), (dx, dy, side) in self.ports.items():
            if self.net[sid][k] != name or self.net[sid][1 - k]:
                continue
            L = stub_len(sid, side) + APPROACH
            ux, uy = SIDE[side]
            px, py = x + dx, y + dy
            ex, ey = px + ux * L, py + uy * L
            rects.append((min(px, ex) - 0.15, min(py, ey) - 0.15, max(px, ex) + 0.15, max(py, ey) + 0.15))
        return rects

    def initial(self) -> dict:
        pos = {}
        for n in self.names:
            spec = self.fixed.get(n) or self.hints.get(n)
            if spec:
                pos[n] = (spec[0], spec[1], spec[2] if len(spec) > 2 else self.dr.blocks[n].mod)
        todo = [n for n in self.names if n not in pos]
        while todo:
            todo.sort(key=lambda n: -sum(1 for ends in self.net.values() if n in ends
                                         and any(e in pos for e in ends if e and e != n)))
            n = todo.pop(0)
            pos[n] = self.auto_place(n, pos)
        return self.align(pos)

    def align(self, pos, tol=0.15):
        """Line up units joined port-to-port (outlet facing inlet) so the stream between them is one straight run.
        Columns never move; otherwise the downstream unit moves."""
        pos = dict(pos)
        for _ in range(3):
            for sid, (s, d) in self.net.items():
                if not (s and d) or (sid, 0) not in self.ports or (sid, 1) not in self.ports:
                    continue
                a, b = self.ports[(sid, 0)], self.ports[(sid, 1)]
                pa = (pos[s][0] + a[0], pos[s][1] + a[1])
                pb = (pos[d][0] + b[0], pos[d][1] + b[1])
                mover = d if self.dr.blocks[d].icon != "FRACT1" else s
                if self.dr.blocks[mover].icon == "FRACT1":
                    continue
                sign = 1 if mover == d else -1
                if {a[2], b[2]} == {"r", "l"} and 1e-6 < abs(pa[1] - pb[1]) < tol and (pb[0] - pa[0]) * SIDE[a[2]][0] > 0:
                    x, y, m = pos[mover]
                    pos[mover] = (x, round(y + sign * (pa[1] - pb[1]), 4), m)
                elif {a[2], b[2]} == {"u", "d"} and 1e-6 < abs(pa[0] - pb[0]) < tol and (pb[1] - pa[1]) * SIDE[a[2]][1] > 0:
                    x, y, m = pos[mover]
                    pos[mover] = (round(x + sign * (pa[0] - pb[0]), 4), y, m)
        return pos

    def auto_place(self, n, pos, avoid=()):
        mod = self.dr.blocks[n].mod
        nbrs = [(sid, k) for (sid, k) in self.ports if self.net[sid][k] == n and self.net[sid][1 - k] in pos]
        taken = [r for m, (x, y, _) in pos.items() if m != n for r in self.footprint(m, x, y)]
        anchors = [pos[self.net[sid][1 - k]][:2] for sid, k in nbrs] or [(6.0, -1.5)]
        best, best_score = None, 1e18
        for ax, ay in anchors:
            for i in range(-7, 8):
                for j in range(-6, 7):
                    x, y = round(ax + 0.6 * i, 2), round(ay + 0.6 * j, 2)
                    if (x, y) in avoid:
                        continue
                    fp = self.footprint(n, x, y)
                    if any(_rect_hit(r, t, 0.3) for r in fp for t in taken):
                        continue
                    score = 0.0
                    for sid, k in nbrs:
                        mx, my, _ = pos[self.net[sid][1 - k]]
                        p = self.ports[(sid, k)]
                        q = self.ports.get((sid, 1 - k), (0, 0, "r"))
                        a, c = (x + p[0], y + p[1]), (mx + q[0], my + q[1])
                        dist = abs(a[0] - c[0]) + abs(a[1] - c[1])
                        score += dist
                        ux, uy = SIDE[p[2]]
                        if (c[0] - a[0]) * ux + (c[1] - a[1]) * uy < 0:
                            score += 1.5                  # port faces away from the other end
                        if dist < label_w(sid) + 2 * VIS + 2 * APPROACH:
                            score += 5.0                  # no room for the ID box
                    if score < best_score:
                        best, best_score = (x, y, mod), score
        return best or (anchors[0][0] + 2.0, anchors[0][1] - 2.0, mod)

    # ---- routing
    def plan(self, pos) -> Plan:
        dr, net, ports = self.dr, self.net, self.ports
        pos = self.align(pos)
        rects = [r for n, (x, y, _) in pos.items() for r in block_rects(dr.blocks[n], x, y, self.ports_of(n))]
        port_xy = {}
        for (sid, k), (dx, dy, side) in ports.items():
            n = net[sid][k]
            if n in pos:
                port_xy[(sid, k)] = (round(pos[n][0] + dx, 4), round(pos[n][1] + dy, 4), side)
        # streams sharing one port point are spread along that side, ordered by where their other end is
        attach, groups = {}, {}
        for key, (px, py, side) in port_xy.items():
            groups.setdefault((net[key[0]][key[1]], round(px, 3), round(py, 3), side), []).append(key)
        for (n, px, py, side), keys in groups.items():
            if len(keys) < 2:
                continue

            def other_end(key):
                sid, k = key
                o = net[sid][1 - k]
                if o in pos and (sid, 1 - k) in ports:
                    return pos[o][0] + ports[(sid, 1 - k)][0], pos[o][1] + ports[(sid, 1 - k)][1]
                ux, uy = SIDE[side]
                return px + ux, py + uy
            along_y = side in "lr"
            keys.sort(key=lambda kk: -other_end(kk)[1] if along_y else other_end(kk)[0])
            body = block_rects(dr.blocks[n], pos[n][0], pos[n][1], self.ports_of(n))[0]
            room = 0.8 * ((body[3] - body[1]) if along_y else (body[2] - body[0]))
            # wide enough for two arrows (and two ID boxes, for feeds/products) side by side when there is room
            step = min(max(0.4, box_h() + 0.12), room / (len(keys) - 1))
            for i, key in enumerate(keys):
                off = (i - (len(keys) - 1) / 2) * step
                d = self.attach_override.get(key, (0.0, -off) if along_y else (off, 0.0))
                attach[key] = d
                port_xy[key] = (round(px + d[0], 4), round(py + d[1], 4), side)
        keys_x, keys_y = [], []
        for px, py, side in port_xy.values():
            ux, uy = SIDE[side]
            keys_x += [px, px + ux * APPROACH]
            keys_y += [py, py + uy * APPROACH]
        stubs = {}
        for sid, (s, d) in net.items():
            if s and d:
                continue
            k = 0 if s else 1
            if (sid, k) not in port_xy:
                continue
            px, py, side = port_xy[(sid, k)]
            ux, uy = SIDE[side]
            L = stub_len(sid, side) + APPROACH
            stubs[sid] = (k, (px + ux * L, py + uy * L), (px, py, side))
        all_x = [r[0] for r in rects] + [r[2] for r in rects] + keys_x + [v[1][0] for v in stubs.values()]
        all_y = [r[1] for r in rects] + [r[3] for r in rects] + keys_y + [v[1][1] for v in stubs.values()]
        bounds = (math.floor(min(all_x)) - 2.0, math.floor(min(all_y)) - 2.0, math.ceil(max(all_x)) + 2.0,
                  math.ceil(max(all_y)) + 2.0)
        router = Router(bounds, rects, keys_x, keys_y)
        streams, paths, issues, total = {}, {}, [], 0.0
        for sid, (k, at, port) in sorted(stubs.items()):
            ux, uy = SIDE[port[2]]
            pre = (port[0] + ux * APPROACH, port[1] + uy * APPROACH)
            route = [at, pre, port]
            toward = (-ux, -uy)
            lab = (toward[0] * (ARROW + label_w(sid) / 2), 0.0) if port[2] in "lr" else (0.0, toward[1] * (ARROW + box_h() / 2))
            streams[sid] = stream_object(sid, 2 if k == 0 else 1, at, lab, route if k == 0 else None,
                                         route if k == 1 else None, attach.get((sid, 0)), attach.get((sid, 1)))
            paths[sid] = [at, pre, port[:2]]
            box = text_rect(at[0] + lab[0], at[1] + lab[1], sid)
            ar = arrow_rect(at, toward)
            ar_c = (ar[0] - CLEAR, ar[1] - CLEAR, ar[2] + CLEAR, ar[3] + CLEAR)
            if router.segments_hit_rect(ar_c, sid) or router.segments_hit_rect(box) or \
                    any(_rect_hit(box, r) or _rect_hit(ar_c, r) for r in router.rects):
                issues.append(f"STUB-BLOCKED {sid}")
            router.add_path(paths[sid], sid)
            router.add_rect(ar, pad=CLEAR + 0.02)
            router.add_rect(box, pad=0.05)
        internal = [sid for sid, (s, d) in net.items() if s and d and (sid, 0) in port_xy and (sid, 1) in port_xy]

        def manhattan(sid):
            a, b = port_xy[(sid, 0)], port_xy[(sid, 1)]
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        for sid in sorted(internal, key=manhattan):
            a, b = port_xy[(sid, 0)], port_xy[(sid, 1)]
            ua, ub = SIDE[a[2]], SIDE[b[2]]
            sa = (a[0] + ua[0] * APPROACH, a[1] + ua[1] * APPROACH)
            sb = (b[0] + ub[0] * APPROACH, b[1] + ub[1] * APPROACH)
            mid = router.route(sa, a[2], sb, OPP[b[2]], sid)
            if mid is None:
                issues.append(f"NO-ROUTE {sid}")
                continue
            full = _simplify([a[:2]] + mid + [b[:2]])
            segs = list(zip(full, full[1:]))
            seg_len = [abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in segs]
            total_len = sum(seg_len)
            best, best_d, run = None, 1e18, 0.0
            for (p, q), L in zip(segs, seg_len):
                horizontal = abs(p[1] - q[1]) < 1e-9
                if L >= (label_w(sid) if horizontal else box_h()) + 2 * VIS:
                    c = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                    rect = text_rect(*c, sid)
                    if not router.segments_hit_rect(rect, sid) and not any(_rect_hit(rect, r) for r in router.rects):
                        d = abs(run + L / 2 - total_len / 2)
                        if d < best_d:
                            best, best_d = c, d
                run += L
            if best is None:
                issues.append(f"NO-LABEL-ROOM {sid}")
                p, q = segs[max(range(len(segs)), key=lambda i: seg_len[i])]
                best = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            at = best
            i = next(j for j, (p, q) in enumerate(segs) if min(p[0], q[0]) - 1e-9 <= at[0] <= max(p[0], q[0]) + 1e-9
                     and min(p[1], q[1]) - 1e-9 <= at[1] <= max(p[1], q[1]) + 1e-9)
            r0 = _simplify([at] + full[:i + 1][::-1])
            r1 = _simplify([at] + full[i + 1:])
            r0[-1] = (*r0[-1], a[2])
            r1[-1] = (*r1[-1], b[2])
            streams[sid] = stream_object(sid, 0, at, (0.0, 0.0), r0, r1, attach.get((sid, 0)), attach.get((sid, 1)))
            paths[sid] = full
            router.add_path(full, sid)
            router.add_rect(text_rect(*at, sid), pad=0.03)
            total += total_len
        blocks_out = {}
        for n, (x, y, mod) in pos.items():
            b = dr.blocks[n]
            if n in self.keep_labels:
                blocks_out[n] = (x, y, mod, (b.lx, b.ly))
                continue
            rb = block_rects(b, x, y, self.ports_of(n))
            top, bot = max(r[3] for r in rb) - y, min(r[1] for r in rb) - y
            right, left = max(r[2] for r in rb) - x, min(r[0] for r in rb) - x
            w, h = _u(13.9 * len(n) + 6), _u(28)            # block names are a fixed screen size too
            g = CLEAR / 2 + 0.02
            cands = [(right + w / 2 + g, top + h / 2 + g), (left - w / 2 - g, top + h / 2 + g),
                     (right + w / 2 + g, bot - h / 2 - g), (left - w / 2 - g, bot - h / 2 - g),
                     (0.0, top + h / 2 + g), (0.0, bot - h / 2 - g),
                     (right + w / 2 + g, top + h + 2 * g), (left - w / 2 - g, top + h + 2 * g),
                     (right + w / 2 + g, 0.0), (left - w / 2 - g, 0.0)]
            chosen = None
            for c in cands:
                r = text_rect(x + c[0], y + c[1], n, boxed=False)
                if not router.segments_hit_rect((r[0] - CLEAR / 2, r[1] - CLEAR / 2, r[2] + CLEAR / 2, r[3] + CLEAR / 2)) \
                        and not any(_rect_hit(r, rr, CLEAR / 2) for rr in router.rects):
                    chosen = c
                    break
            if chosen is None:
                issues.append(f"NO-BLOCK-LABEL-ROOM {n}")
                chosen = cands[0]
            router.add_rect(text_rect(x + chosen[0], y + chosen[1], n, boxed=False), pad=0.0)
            blocks_out[n] = (x, y, mod, (chosen[0] / b.scale, chosen[1] / b.scale))   # Aspen scales it with the icon
        return Plan(blocks_out, streams, paths, issues, count_crossings(paths), total)

    @staticmethod
    def extents(plan: Plan, margin=0.6):
        xs = [p[0] for pts in plan.paths.values() for p in pts] + [v[0] for v in plan.blocks.values()]
        ys = [p[1] for pts in plan.paths.values() for p in pts] + [v[1] for v in plan.blocks.values()]
        return min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin

    @classmethod
    def fit_zoom(cls, plan: Plan, pane_px=(1850, 950)) -> float:
        """Largest zoom at which the whole drawing fits a typical flowsheet pane."""
        x0, x1, y0, y1 = cls.extents(plan)
        return min(pane_px[0] / (x1 - x0), pane_px[1] / (y1 - y0)) / PX_PER_UNIT

    def write(self, plan: Plan) -> str:
        """Drawing text, saved with the zoom it was planned for (the GUI opens at the zoom stored for the flowsheet
        pane, not at the VIEWPORT)."""
        x0, x1, y0, y1 = self.extents(plan)
        text = self.dr.render(plan.blocks, plan.streams, size=(x0, x1, y0, y1), viewport=(x0, x1, y1, y0))
        return activate_flowsheet(set_flowsheet_zoom(text, ZOOM))

    def suspects(self, plan: Plan) -> set:
        """Blocks touching a stream or block named in an issue or a crossing."""
        names = set()
        crossing = {sid for sid, pts in plan.paths.items() for sid2, pts2 in plan.paths.items() if sid != sid2
                    and any(_crosses(a, b, c, d) for a, b in zip(pts, pts[1:]) for c, d in zip(pts2, pts2[1:]))}
        words = {w for i in plan.issues for w in i.split()[1:]} | crossing
        for w in words:
            if w in self.net:
                names |= {e for e in self.net[w] if e}
            elif w in self.dr.blocks:
                names.add(w)
        return names

    def improve(self, pos, movable, rounds=3, steps=(0.3, 0.6, 1.2), log=None):
        """Greedy repair: nudge the blocks involved in problems while the score drops. A sideways move also shifts the
        units downstream of it in the same row, and leaving the planned row costs extra, so trains stay straight."""
        start = dict(pos)

        def score(p, ps):
            drift = sum(3.0 * abs(ps[n][1] - start[n][1]) + 0.3 * abs(ps[n][0] - start[n][0]) for n in ps)
            return p.score + drift

        best = self.plan(pos)
        best_s = score(best, pos)
        for _ in range(rounds):
            if not best.issues and not best.crossings:
                break
            improved = False
            for n in [m for m in movable if m in self.suspects(best)]:
                for dx, dy in [(s * a, s * b) for s in steps for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1))]:
                    x, y, mod = pos[n]
                    trial = dict(pos)
                    row = [m for m in movable if abs(pos[m][1] - y) < 0.3 and (pos[m][0] - x) * (dx or 1) > 0] if dx else []
                    for m in [n] + row:
                        mx, my, mm = pos[m]
                        trial[m] = (round(mx + dx, 3), round(my + dy, 3), mm)
                    p = self.plan(trial)
                    s_ = score(p, trial)
                    if s_ < best_s - 1e-6:
                        pos, best, best_s, improved = trial, p, s_, True
                        if log:
                            log(f"  moved {n} by ({dx:+.1f}, {dy:+.1f}): {len(p.issues)} issues, {p.crossings} crossings")
            if not improved or not best.issues and not best.crossings:
                break
        return pos, best


# --------------------------------------------------------------------------------------------- audit
def check(saved_text: str, net: dict, intended: dict | None = None, ports_of=None) -> list:
    """Problems in a drawing as Aspen saved it (judged at the zoom saved in it). ``intended`` = ``{sid: stream object
    text}`` that was written."""
    z = flowsheet_zoom(saved_text)
    if z:
        set_zoom(z)
    dr = Drawing.parse(saved_text)
    issues = []
    streams = {sid: parse_stream(raw) for sid, raw in dr.streams.items() if sid in net}
    blocks = {n: b for n, b in dr.blocks.items() if any(n in ends for ends in net.values())}
    rects = {n: block_rects(b, ports=(ports_of or {}).get(n, ())) for n, b in blocks.items()}
    segs = []
    for sid, st in streams.items():
        for r in st["routes"]:
            if r:
                for p, q in zip(r[0], r[0][1:]):
                    if abs(p[0] - q[0]) + abs(p[1] - q[1]) > EPS:
                        segs.append((sid, p, q))
                        if abs(p[0] - q[0]) > 1e-3 and abs(p[1] - q[1]) > 1e-3:
                            issues.append(f"DIAGONAL {sid}")
    for sid, (s, d) in net.items():
        st = streams.get(sid)
        if not st:
            issues.append(f"NO-DRAWING {sid}")
            continue
        if s and not st["routes"][0]:
            issues.append(f"UNROUTED-SOURCE {sid}")
        if d and not st["routes"][1]:
            issues.append(f"UNROUTED-DEST {sid}")

    def overlap(a, b, c, d):
        if abs(a[1] - b[1]) < EPS and abs(c[1] - d[1]) < EPS and abs(a[1] - c[1]) < NEAR - 0.02:
            return min(max(a[0], b[0]), max(c[0], d[0])) - max(min(a[0], b[0]), min(c[0], d[0])) > 0.02
        if abs(a[0] - b[0]) < EPS and abs(c[0] - d[0]) < EPS and abs(a[0] - c[0]) < NEAR - 0.02:
            return min(max(a[1], b[1]), max(c[1], d[1])) - max(min(a[1], b[1]), min(c[1], d[1])) > 0.02
        return False

    for i, (s1, a, b) in enumerate(segs):
        for s2, c, d in segs[i + 1:]:
            if s1 != s2 and _crosses(a, b, c, d):
                issues.append(f"CROSS {s1} x {s2}")
            elif s1 != s2 and overlap(a, b, c, d):
                issues.append(f"OVERLAP {s1} / {s2}")
        for n, rs in rects.items():
            if any(_seg_rect(a, b, (r[0] + 0.06, r[1] + 0.06, r[2] - 0.06, r[3] - 0.06)) for r in rs):
                issues.append(f"THROUGH-BLOCK {s1} / {n}")
    for sid, st in streams.items():
        for r in st["routes"]:
            if r:
                for p, q, w in zip(r[0], r[0][1:], r[0][2:]):
                    if abs(p[1] - q[1]) < EPS and abs(q[1] - w[1]) < EPS and (q[0] - p[0]) * (w[0] - q[0]) < -1e-9:
                        issues.append(f"BACKTRACK {sid}")
                    if abs(p[0] - q[0]) < EPS and abs(q[0] - w[0]) < EPS and (q[1] - p[1]) * (w[1] - q[1]) < -1e-9:
                        issues.append(f"BACKTRACK {sid}")
    labels, arrows = {}, {}
    for sid, st in streams.items():
        c = (st["at"][0] + st["label"][0], st["at"][1] + st["label"][1])
        labels["s:" + sid] = text_rect(*c, sid)
        if st["term"] in (1, 2):
            r = st["routes"][1 if st["term"] == 1 else 0]
            if r and len(r[0]) > 1:
                (x0, y0), (x1, y1) = r[0][0], r[0][1]
                L = abs(x1 - x0) + abs(y1 - y0) or 1
                arrows[sid] = arrow_rect(r[0][0], ((x1 - x0) / L, (y1 - y0) / L))
        else:
            # the ID box sits at At on the line: each side needs visible line before the next bend
            for r in st["routes"]:
                if r and len(r[0]) > 1:
                    run = _coarse(r[0], tol=0.01, snap=False)        # one straight run, even across Aspen's 0.001 jogs
                    (x0, y0), (x1, y1) = run[0], run[1]
                    L = abs(x1 - x0) + abs(y1 - y0)
                    half = label_w(sid) / 2 if abs(y1 - y0) < EPS else box_h() / 2
                    if L < half + 0.15:
                        issues.append(f"LABEL-GAP {sid}")
                        break
    for n, b in blocks.items():
        if True:
            labels["b:" + n] = text_rect(b.x + b.lx * b.scale, b.y + b.ly * b.scale, n, boxed=False)
    keys = list(labels)
    for i, k in enumerate(keys):
        box = labels[k]
        for k2 in keys[i + 1:]:
            if _rect_hit(box, labels[k2]):
                issues.append(f"LABEL-LABEL {k} / {k2}")
        for n, rs in rects.items():
            if k != "b:" + n and any(_rect_hit(box, r, -0.02) for r in rs):
                issues.append(f"LABEL-ON-BLOCK {k} / {n}")
        pad = CLEAR / 2 if k.startswith("b:") else 0.0
        for s1, a, b in segs:
            if k != "s:" + s1 and _seg_rect(a, b, box, pad):
                issues.append(f"LABEL-ON-LINE {k} / {s1}")
        for sid, ar in arrows.items():
            if _rect_hit(box, ar, 0.06):
                issues.append(f"LABEL-ON-ARROW {k} / {sid}")
    sids = list(arrows)
    for i, a in enumerate(sids):
        for b2 in sids[i + 1:]:
            if _rect_hit(arrows[a], arrows[b2], 0.05):
                issues.append(f"ARROW-ARROW {a} / {b2}")
        for s1, p, q in segs:
            if s1 != a and _seg_rect(p, q, arrows[a], CLEAR - 0.02):
                issues.append(f"LINE-ON-ARROW {s1} / {a}")
    if intended:
        for sid, raw in intended.items():
            want, got = parse_stream(raw), streams.get(sid)
            if not got:
                continue
            for r_w, r_g in zip(want["routes"], got["routes"]):
                if bool(r_w) != bool(r_g) or (r_w and _coarse(r_w[0]) != _coarse(r_g[0])):
                    issues.append(f"ASPEN-REROUTED {sid}")
                    break
    return sorted(set(issues))


def _coarse(pts, tol=0.06, snap=True):
    """Shape of a route without Aspen's 0.001 snaps and shortened approach stubs."""
    out = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    out.append(pts[-1])
    res = [out[0]]
    for a, b in zip(out[1:], out[2:]):
        pa = res[-1]
        if not ((abs(pa[0] - a[0]) < tol and abs(a[0] - b[0]) < tol) or (abs(pa[1] - a[1]) < tol and abs(a[1] - b[1]) < tol)):
            res.append(a)
    res.append(out[-1])
    return [(round(p[0] / 0.05), round(p[1] / 0.05)) for p in res] if snap else res


def render_png(saved_text: str, net: dict, png: str, title: str = "", ports_of=None) -> None:
    """Schematic of a saved drawing (blocks, routes, ID boxes, arrows) for quick review."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dr = Drawing.parse(saved_text)
    fig, ax = plt.subplots(figsize=(18, 11))
    for n, b in dr.blocks.items():
        if not any(n in ends for ends in net.values()):
            continue
        for r in block_rects(b, ports=(ports_of or {}).get(n, ())):
            ax.add_patch(plt.Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fill=False, lw=1.3))
        if b.icon == "FRACT1":
            ax.text(b.x, b.y, n, ha="center", va="center", fontsize=7, weight="bold", rotation=90)
        else:
            r = text_rect(b.x + b.lx * b.scale, b.y + b.ly * b.scale, n, boxed=False)
            ax.text((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, n, ha="center", va="center", fontsize=7, weight="bold")
    for sid, raw in dr.streams.items():
        if sid not in net:
            continue
        st = parse_stream(raw)
        for r in st["routes"]:
            if r:
                ax.plot(*zip(*r[0]), lw=1, color="black")
        c = (st["at"][0] + st["label"][0], st["at"][1] + st["label"][1])
        r = text_rect(*c, sid)
        ax.add_patch(plt.Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fc="white", ec="black", lw=0.7, zorder=3))
        ax.text(*c, sid, ha="center", va="center", fontsize=6.5, zorder=4)
        if st["term"] in (1, 2):
            rr = st["routes"][1 if st["term"] == 1 else 0]
            if rr and len(rr[0]) > 1:
                (x0, y0), (x1, y1) = rr[0][0], rr[0][1]
                L = abs(x1 - x0) + abs(y1 - y0) or 1
                a = arrow_rect(rr[0][0], ((x1 - x0) / L, (y1 - y0) / L))
                ax.add_patch(plt.Rectangle((a[0], a[1]), a[2] - a[0], a[3] - a[1], fc="#cfe8ff", ec="tab:blue", lw=0.7,
                                           zorder=2))
    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_title(title)
    fig.savefig(png, dpi=95, bbox_inches="tight")
    plt.close(fig)
