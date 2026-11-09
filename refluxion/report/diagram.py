"""Automatic flowsheet drawing (process flow diagram) as SVG.

Layout: recycle loops are cut at their tear streams, blocks are ranked by longest path from the
feeds (columns of the drawing), ordered within a rank by barycenter sweeps, and streams are routed
orthogonally. Recycles return along a lane beneath the drawing. Column feeds enter at a height
proportional to their feed stage, like Aspen's column icon.
"""

from __future__ import annotations

import html

SIZES = {  # icon: (width, height)
    "column": (46, 170), "flash": (44, 86), "heater": (46, 46), "heatx": (54, 54), "mixer": (42, 42),
    "splitter": (42, 42), "reactor": (58, 90), "pump": (46, 46), "compressor": (54, 46), "valve": (38, 26),
    "sep": (54, 76), "dupl": (42, 42), "box": (64, 46),
}
DX, ROW_GAP, MARGIN = 200, 70, 40


def _e(s):
    return html.escape(str(s), quote=True)


def _layout(fs):
    if not fs.sequence:          # keep the solved loops (and their convergence history) if already built
        fs._build_sequence()
    tears = {s.name for loop in fs.loops for s in loop.tears}
    names = list(fs.blocks)
    preds = {n: [] for n in names}
    for s in fs.streams.values():
        if s.source and s.dest and s.name not in tears:
            a, b = s.source[0].name, s.dest[0].name
            if a != b:
                preds[b].append(a)
    rank = {}

    def r(n, seen=()):
        if n in rank:
            return rank[n]
        if n in seen:
            return 0
        rank[n] = 0 if not preds[n] else 1 + max(r(p, seen + (n,)) for p in preds[n])
        return rank[n]

    for n in names:
        r(n)
    order = {}
    for n in names:
        order.setdefault(rank[n], []).append(n)
    pos = {}
    for _ in range(4):
        for k in sorted(order):
            for i, n in enumerate(order[k]):
                pos[n] = i
            if k == 0:
                continue

            def bary(n):
                ps = [pos[p] for p in preds[n] if p in pos]
                return sum(ps) / len(ps) if ps else pos.get(n, 0)

            order[k].sort(key=bary)
    coords, sizes = {}, {}
    max_h = 0
    for k in sorted(order):
        y = MARGIN + 30
        for n in order[k]:
            w, h = SIZES.get(fs.blocks[n].icon, SIZES["box"])
            coords[n] = (MARGIN + 180 + k * DX, y + h / 2)
            sizes[n] = (w, h)
            y += h + ROW_GAP
        max_h = max(max_h, y)
    for k in sorted(order):  # center each column vertically
        col = order[k]
        top = min(coords[n][1] - sizes[n][1] / 2 for n in col)
        bot = max(coords[n][1] + sizes[n][1] / 2 for n in col)
        shift = (max_h - ROW_GAP - (bot - top)) / 2 - (top - MARGIN - 30)
        for n in col:
            coords[n] = (coords[n][0], coords[n][1] + shift)
    return coords, sizes, tears, max_h


def _ports(block, coords, sizes):
    """{("in"|"out", stream name): (x, y, side)} for one block."""
    x, y = coords[block.name]
    w, h = sizes[block.name]
    left, right, top, bottom = x - w / 2, x + w / 2, y - h / 2, y + h / 2
    out = {}
    ins = block.all_inlets()
    if block.icon == "column":
        N = int(block.spec.get("n_stages", 10)) if block.type_name == "RadFrac" else 10
        feeds = block.spec.get("feeds", {})
        for s in ins:
            st = feeds.get(s.name, N // 2)
            st = st[0] if isinstance(st, tuple) else st
            fy = top + 14 + (h - 28) * (int(st) - 1) / max(N - 1, 1)
            out[("in", s.name)] = (left, fy, "left")
    elif block.type_name == "HeatX":
        for s in block.inlets.get("hot_in", []):
            out[("in", s.name)] = (left, y - h * 0.25, "left")
        for s in block.inlets.get("cold_in", []):
            out[("in", s.name)] = (left, y + h * 0.25, "left")
    else:
        n = len(ins)
        for i, s in enumerate(ins):
            out[("in", s.name)] = (left, y + (i - (n - 1) / 2) * min(16, h / max(n, 1)), "left")
    outs = list(block.outlets.items())
    for i, (port, s) in enumerate(outs):
        if block.icon in ("flash", "column"):
            upper = port in ("vapor", "distillate")
            out[("out", s.name)] = (x, top, "top") if upper else (x, bottom, "bottom")
        elif block.type_name == "HeatX":
            out[("out", s.name)] = (right, y - h * 0.25 if port == "hot_out" else y + h * 0.25, "right")
        else:
            n = len(outs)
            out[("out", s.name)] = (right, y + (i - (n - 1) / 2) * min(18, h / max(n, 1)), "right")
    return out


def _icon(block, x, y, w, h):
    kind = block.icon
    l, t = x - w / 2, y - h / 2
    if kind == "column":
        trays = "".join(f'<line class="blk-in" x1="{l + 6}" x2="{l + w - 6}" y1="{t + 22 + i * (h - 44) / 6:.1f}" '
                        f'y2="{t + 22 + i * (h - 44) / 6:.1f}"/>' for i in range(7))
        return f'<rect class="blk" x="{l}" y="{t}" width="{w}" height="{h}" rx="{w / 2}"/>{trays}'
    if kind == "flash":
        return f'<rect class="blk" x="{l}" y="{t}" width="{w}" height="{h}" rx="{w / 2}"/>'
    if kind in ("heater", "heatx"):
        rr = w / 2
        zig = (f'M{x - rr * 0.8:.1f},{y:.1f} l{rr * 0.3:.1f},{-rr * 0.45:.1f} l{rr * 0.35:.1f},{rr * 0.9:.1f} '
               f'l{rr * 0.35:.1f},{-rr * 0.9:.1f} l{rr * 0.3:.1f},{rr * 0.45:.1f}')
        extra = (f'<line class="blk-in" x1="{x - rr}" x2="{x + rr}" y1="{y + rr * 0.5:.1f}" y2="{y + rr * 0.5:.1f}"/>'
                 if kind == "heatx" else "")
        return f'<circle class="blk" cx="{x}" cy="{y}" r="{rr}"/><path class="blk-in" d="{zig}"/>{extra}'
    if kind == "mixer":
        return f'<polygon class="blk" points="{l},{t} {l + w},{y} {l},{t + h}"/>'
    if kind == "splitter":
        return f'<polygon class="blk" points="{l + w},{t} {l},{y} {l + w},{t + h}"/>'
    if kind == "reactor":
        return (f'<rect class="blk" x="{l}" y="{t}" width="{w}" height="{h}" rx="16"/>'
                f'<path class="blk-in" d="M{l + 8},{t + 20} L{l + w - 8},{t + h - 20} M{l + w - 8},{t + 20} L{l + 8},{t + h - 20}"/>')
    if kind == "pump":
        return (f'<circle class="blk" cx="{x}" cy="{y}" r="{w / 2}"/>'
                f'<polygon class="blk-in" points="{x - 9},{y - 11} {x + 13},{y} {x - 9},{y + 11}"/>')
    if kind == "compressor":
        return f'<polygon class="blk" points="{l},{t} {l + w},{t + h * 0.25} {l + w},{t + h * 0.75} {l},{t + h}"/>'
    if kind == "valve":
        return (f'<polygon class="blk" points="{l},{t} {x},{y} {l},{t + h}"/>'
                f'<polygon class="blk" points="{l + w},{t} {x},{y} {l + w},{t + h}"/>')
    return f'<rect class="blk" x="{l}" y="{t}" width="{w}" height="{h}" rx="6"/>'


STANDALONE = (
    "<style>.bg{fill:#fcfcfb}.blk{fill:#f3f2ee;stroke:#52514e;stroke-width:1.5}.blk-in{fill:none;stroke:#898781;stroke-width:1.2}"
    ".st{fill:none;stroke:#52514e;stroke-width:1.6}.st-tear{stroke-dasharray:6 4}.hit{display:none}"
    ".bname{fill:#0b0b0b;font:600 12px system-ui,sans-serif}.btype{fill:#898781;font:11px system-ui,sans-serif}"
    ".pill rect{fill:#fcfcfb;stroke:#c3c2b7}.pill text{fill:#0b0b0b;font:11px ui-monospace,Consolas,monospace}"
    "#arr path{fill:#52514e}@media (prefers-color-scheme: dark){.bg{fill:#1a1a19}.blk{fill:#242423;stroke:#c3c2b7}"
    ".st{stroke:#c3c2b7}.bname{fill:#fff}.pill rect{fill:#1a1a19;stroke:#383835}.pill text{fill:#fff}#arr path{fill:#c3c2b7}}</style>"
)


def flowsheet_svg(fs, standalone: bool = False, stream_info: dict | None = None) -> str:
    """Draw the flowsheet. ``stream_info`` maps stream name -> tooltip text."""
    coords, sizes, tears, max_h = _layout(fs)
    ports = {}
    for b in fs.blocks.values():
        for k, v in _ports(b, coords, sizes).items():
            ports[(k[0], k[1], b.name)] = v
    xs = [c[0] for c in coords.values()] or [0]
    width = max(xs) + 230 + MARGIN
    lane_y = max_h + 10
    height = lane_y + 44 + 12 * len(tears)
    parts, labels = [], []
    lane_i = 0
    feed_spots = []
    for s in fs.streams.values():
        info = (stream_info or {}).get(s.name, "")
        cls = "st"
        if s.source is None and s.dest is not None:
            x2, y2, _ = ports[("in", s.name, s.dest[0].name)]
            x1 = x2 - 95
            # stagger labels of feeds that enter close together (e.g. adjacent column stages)
            while any(abs(y2 - fy) < 18 and abs(x1 - fx) < 60 for fx, fy in feed_spots):
                x1 -= 70
            feed_spots.append((x1, y2))
            path = f"M{x1:.1f},{y2:.1f} H{x2:.1f}"
            cls += " st-feed"
            lx, ly = x1 + 6, y2 - 9
        elif s.dest is None and s.source is not None:
            x1, y1, side = ports[("out", s.name, s.source[0].name)]
            if side == "top":
                path = f"M{x1:.1f},{y1:.1f} V{y1 - 22:.1f} H{x1 + 95:.1f}"
                lx, ly = x1 + 12, y1 - 31
            elif side == "bottom":
                path = f"M{x1:.1f},{y1:.1f} V{y1 + 42:.1f} H{x1 + 95:.1f}"
                lx, ly = x1 + 30, y1 + 34
            else:
                path = f"M{x1:.1f},{y1:.1f} H{x1 + 95:.1f}"
                lx, ly = x1 + 12, y1 - 9
            cls += " st-prod"
        elif s.source is not None and s.dest is not None:
            x1, y1, side = ports[("out", s.name, s.source[0].name)]
            x2, y2, _ = ports[("in", s.name, s.dest[0].name)]
            sx, sy = x1, y1
            pre = f"M{x1:.1f},{y1:.1f}"
            if side in ("top", "bottom"):
                sy = y1 - 22 if side == "top" else y1 + 42
                pre += f" V{sy:.1f}"
            if s.name in tears or x2 <= sx + 20:
                lane = lane_y + 12 * lane_i
                lane_i += 1
                ex = sx + (46 if side in ("top", "bottom") else 18)
                path = f"{pre} H{ex:.1f} V{lane:.1f} H{x2 - 26:.1f} V{y2:.1f} H{x2:.1f}"
                if s.name in tears:
                    cls += " st-tear"
                lx, ly = (ex + x2 - 26) / 2 - 20, lane - 6
            else:
                mid = (sx + x2) / 2
                path = f"{pre} H{mid:.1f} V{y2:.1f} H{x2:.1f}"
                lx, ly = mid + 6, (min(sy, y2) - 8) if abs(sy - y2) > 4 else sy - 8
        else:
            continue
        parts.append(f'<g class="stg" data-stream="{_e(s.name)}" data-info="{_e(info)}">'
                     f'<path class="hit" d="{path}"/><path class="{cls}" d="{path}" marker-end="url(#arr)"/></g>')
        labels.append((lx, ly, s.name))
    blocks = []
    for b in fs.blocks.values():
        x, y = coords[b.name]
        w, h = sizes[b.name]
        blocks.append(f'<g class="bk" data-block="{_e(b.name)}" tabindex="0"><title>{_e(b.name)} ({_e(b.type_name)})</title>'
                      f'{_icon(b, x, y, w, h)}'
                      f'<text class="bname" x="{x}" y="{y + h / 2 + 16}" text-anchor="middle">{_e(b.name)}</text>'
                      f'<text class="btype" x="{x}" y="{y + h / 2 + 29}" text-anchor="middle">{_e(b.type_name)}</text></g>')
    lab = []
    for lx, ly, name in labels:
        wpx = 7.0 * len(name) + 12
        lab.append(f'<g class="pill" data-stream="{_e(name)}"><rect x="{lx - 4:.1f}" y="{ly - 11:.1f}" width="{wpx:.1f}" '
                   f'height="16" rx="8"/><text x="{lx + 2:.1f}" y="{ly + 1:.1f}">{_e(name)}</text></g>')
    style = (STANDALONE + f"<rect class='bg' width='{width:.0f}' height='{height:.0f}'/>") if standalone else ""
    ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
    return (f'<svg class="pfd" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}"{ns} '
            f'role="img" aria-label="Process flow diagram of {_e(fs.name)}">{style}'
            '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>'
            + "".join(parts) + "".join(blocks) + "".join(lab) + "</svg>")
