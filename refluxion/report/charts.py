"""SVG charts (no plotting library needed).

Charts are plain SVG strings styled with CSS classes, so one drawing works in the interactive HTML
report (which adds hover crosshairs, tooltips and clickable legends) and as a standalone ``.svg``
file for the Markdown copy (``standalone=True`` embeds its own light/dark styles).

Design rules (from the dataviz method): thin 2px lines, >= 8px markers with a surface ring,
hairline solid gridlines, one y-axis per chart (different units -> separate charts), categorical
colors assigned in a fixed order, a legend whenever there are two or more series.
"""

from __future__ import annotations

import html
import json
import math

import numpy as np

MAX_SERIES = 8

# validated reference palette (light, dark), slot order matters
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

STANDALONE_STYLE = """
.bg{fill:#fcfcfb}.grid{stroke:#e1e0d9;stroke-width:1}.axis{stroke:#c3c2b7;stroke-width:1}
.tick,.lab{fill:#52514e;font:12px system-ui,-apple-system,'Segoe UI',sans-serif}.ttl{fill:#0b0b0b;font:600 13px system-ui,sans-serif}
.lg{fill:#52514e;font:12px system-ui,sans-serif}.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mk{stroke:#fcfcfb;stroke-width:2}.ref{stroke:#898781;stroke-width:1;stroke-dasharray:4 3}
""" + "".join(f".c{i}{{stroke:{c}}}.f{i}{{fill:{c}}}" for i, c in enumerate(PALETTE_LIGHT)) + """
@media (prefers-color-scheme: dark){.bg{fill:#1a1a19}.grid{stroke:#2c2c2a}.axis{stroke:#383835}.tick,.lab,.lg{fill:#c3c2b7}
.ttl{fill:#ffffff}.mk{stroke:#1a1a19}""" + "".join(f".c{i}{{stroke:{c}}}.f{i}{{fill:{c}}}" for i, c in enumerate(PALETTE_DARK)) + "}"


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def nice_ticks(lo: float, hi: float, n: int = 5):
    if not np.isfinite(lo) or not np.isfinite(hi):
        return [0.0, 1.0]
    if hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    span = hi - lo
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 0.5:
        ticks.append(round(t, 12))
        t += step
    return ticks


def fmt_num(v, sig=4) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    v = float(v)
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e6 or a < 1e-3:
        mant, exp = f"{v:.{max(sig - 1, 1)}e}".split("e")
        mant = mant.rstrip("0").rstrip(".") if "." in mant else mant
        return f"{mant}e{int(exp)}"
    s = f"{v:.{sig}g}"
    if "e" in s:
        s = f"{v:.{max(sig - int(math.floor(math.log10(a))) - 1, 0)}f}"
    return s


def line_chart(series, *, title="", x_label="", y_label="", width=680, height=320, log_y=False,
               markers=None, standalone=False, x_ticks=None, y_min=None, y_max=None, step_x=False,
               ref_lines=None, legend=True, chart_id=None, x_integer=False) -> str:
    """``series``: list of dict(name=, x=[...], y=[...]). Colors follow list order."""
    series = [s for s in series if len(s["x"])][:MAX_SERIES]
    pad_l, pad_r, pad_t, pad_b = 64, 18, 34 if title else 14, 46
    leg_h = 22 if (legend and len(series) > 1) else 0
    W, H = width, height + leg_h
    plot_w, plot_h = W - pad_l - pad_r, height - pad_t - pad_b
    xs = np.concatenate([np.asarray(s["x"], float) for s in series]) if series else np.array([0.0, 1.0])
    ys = np.concatenate([np.asarray(s["y"], float) for s in series]) if series else np.array([0.0, 1.0])
    good_y = ys[np.isfinite(ys)]
    if log_y:
        good_y = good_y[good_y > 0]
    xmin, xmax = (np.nanmin(xs), np.nanmax(xs)) if xs.size else (0, 1)
    if good_y.size == 0:
        good_y = np.array([0.0, 1.0])
    ymin, ymax = float(good_y.min()), float(good_y.max())
    if y_min is not None:
        ymin = y_min
    if y_max is not None:
        ymax = y_max
    if log_y:
        lo, hi = math.floor(math.log10(max(ymin, 1e-300))), math.ceil(math.log10(max(ymax, 1e-300)))
        if hi == lo:
            hi += 1
        yt = [10.0 ** k for k in range(lo, hi + 1)]
        ymin, ymax = yt[0], yt[-1]

        def fy(v):
            v = max(v, 1e-300)
            return pad_t + plot_h * (1 - (math.log10(v) - lo) / (hi - lo))
    else:
        yt = nice_ticks(ymin, ymax)
        ymin, ymax = min(ymin, yt[0]), max(ymax, yt[-1])
        if ymax == ymin:
            ymax = ymin + 1

        def fy(v):
            return pad_t + plot_h * (1 - (v - ymin) / (ymax - ymin))
    if x_ticks is None:
        xt = nice_ticks(xmin, xmax, 6)
        if x_integer:
            xt = [t for t in xt if abs(t - round(t)) < 1e-9]
        xmin, xmax = min(xmin, xt[0]), max(xmax, xt[-1])
    else:
        xt = list(x_ticks)
    if xmax == xmin:
        xmax = xmin + 1

    def fx(v):
        return pad_l + plot_w * (v - xmin) / (xmax - xmin)

    cid = chart_id or f"ch{abs(hash((title, x_label, y_label, len(series)))) % 10**8}"
    out = [None]  # opening tag is written last (it carries the data for the hover layer)
    if standalone:
        out.append(f"<style>{STANDALONE_STYLE}</style><rect class='bg' width='{W}' height='{H}'/>")
    if title:
        out.append(f'<text class="ttl" x="{pad_l}" y="20">{_esc(title)}</text>')
    # grid + ticks
    for t in yt:
        y = fy(t)
        out.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">{_esc(fmt_num(t))}</text>')
    for t in xt:
        x = fx(t)
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{pad_t + plot_h}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{pad_t + plot_h + 18}" text-anchor="middle">{_esc(fmt_num(t))}</text>')
    out.append(f'<line class="axis" x1="{pad_l}" x2="{pad_l + plot_w}" y1="{pad_t + plot_h}" y2="{pad_t + plot_h}"/>')
    out.append(f'<line class="axis" x1="{pad_l}" x2="{pad_l}" y1="{pad_t}" y2="{pad_t + plot_h}"/>')
    if x_label:
        out.append(f'<text class="lab" x="{pad_l + plot_w / 2:.1f}" y="{pad_t + plot_h + 38}" text-anchor="middle">{_esc(x_label)}</text>')
    if y_label:
        out.append(f'<text class="lab" transform="translate(14,{pad_t + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">{_esc(y_label)}</text>')
    for r in ref_lines or []:
        if r.get("axis", "y") == "y":
            y = fy(r["value"])
            out.append(f'<line class="ref" x1="{pad_l}" x2="{pad_l + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>')
            out.append(f'<text class="tick" x="{pad_l + plot_w - 4}" y="{y - 5:.1f}" text-anchor="end">{_esc(r.get("label", ""))}</text>')
        else:
            x = fx(r["value"])
            out.append(f'<line class="ref" x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{pad_t + plot_h}"/>')
            out.append(f'<text class="tick" x="{x + 4:.1f}" y="{pad_t + 12}">{_esc(r.get("label", ""))}</text>')
    data = dict(px=[pad_l, pad_l + plot_w], py=[pad_t, pad_t + plot_h], x=[xmin, xmax], y=[ymin, ymax],
                logy=bool(log_y), xl=x_label, yl=y_label, series=[])
    show_markers = markers if markers is not None else all(len(s["x"]) <= 30 for s in series)
    for i, s in enumerate(series):
        x = np.asarray(s["x"], float)
        y = np.asarray(s["y"], float)
        ok = np.isfinite(x) & np.isfinite(y) & ((y > 0) if log_y else True)
        pts = []
        seg = []
        for xi, yi, good in zip(x, y, ok):
            if good:
                seg.append(f"{fx(xi):.1f},{fy(yi):.1f}")
            elif seg:
                pts.append(seg)
                seg = []
        if seg:
            pts.append(seg)
        out.append(f'<g class="series" data-i="{i}">')
        for sg in pts:
            if len(sg) > 1:
                out.append(f'<polyline class="ln c{i}" points="{" ".join(sg)}"/>')
        if show_markers or len(x[ok]) == 1:
            for xi, yi in zip(x[ok], y[ok]):
                out.append(f'<circle class="mk f{i}" cx="{fx(xi):.1f}" cy="{fy(yi):.1f}" r="4"/>')
        out.append("</g>")
        data["series"].append(dict(name=s["name"], x=[float(v) if np.isfinite(v) else None for v in x],
                                   y=[float(v) if np.isfinite(v) else None for v in y]))
    if legend and len(series) > 1:
        lx, ly = pad_l, height + 6
        for i, s in enumerate(series):
            label = str(s["name"])
            out.append(f'<g class="lgi" data-i="{i}" tabindex="0"><line class="ln c{i}" x1="{lx}" x2="{lx + 16}" y1="{ly + 5}" y2="{ly + 5}"/>'
                       f'<text class="lg" x="{lx + 21}" y="{ly + 9}">{_esc(label)}</text></g>')
            lx += 32 + 7 * len(label)
            if lx > W - 80:
                break
    attrs = (f'class="chart" id="{_esc(cid)}" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
             f'aria-label="{_esc(title or y_label)}"')
    if standalone:
        attrs += ' xmlns="http://www.w3.org/2000/svg"'
    else:
        attrs += f' data-chart="{_esc(json.dumps(data))}"'
    out[0] = f"<svg {attrs}>"
    out.append("</svg>")
    return "".join(out)
