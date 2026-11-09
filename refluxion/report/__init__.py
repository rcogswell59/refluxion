"""Solution reports: one interactive, self-contained HTML file plus a Markdown copy.

    rep = fs.report(title="Flash drum - base case", problem="...problem statement in Markdown...")
    rep.highlight("Ethane overhead", fs["VAPOR"].get("mole_flow", "C2"), ">=", 34.5, unit="lbmol/hr")
    rep.column("COL1", components=["C2", "C3"])
    rep.sensitivity(result, x="COL1.heaters.2", y=["C3 in bottoms"])
    rep.save("reports/flash_base")   # -> flash_base.html, flash_base.md, flash_base_assets/*.svg

The HTML works offline (no CDN), has a unit-set switcher, light/dark theme, hoverable charts and a
clickable flowsheet. The Markdown copy shows the same content in VS Code's preview.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..units import FACTORS, _BASE_SETS, as_unitset
from .charts import fmt_num, line_chart
from .diagram import flowsheet_svg


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def fmt(v) -> str:
    """Aspen-like number formatting (about 6 significant digits, thousands separators)."""
    if v is None:
        return "-"
    if isinstance(v, str):
        return v
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not np.isfinite(v):
        return "-"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e9 or a < 1e-4:
        return f"{v:.4e}"
    if a >= 1e5:
        return f"{v:,.0f}"
    dec = max(0, 5 - int(np.floor(np.log10(a))))
    s = f"{v:,.{dec}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


@dataclass
class Q:
    """A number in SI with its physical quantity - rendered in the report's unit set."""
    si: float
    quantity: str


@dataclass
class Pill:
    text: str
    status: str = "neutral"   # good | warn | bad | neutral


@dataclass
class Section:
    id: str
    title: str
    items: list = field(default_factory=list)
    group: str = "main"


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "section"


# ----------------------------------------------------------------------------------------------
# tiny Markdown subset -> HTML (paragraphs, lists, bold/italic, code, headings, tables, math kept)

def md_to_html(text: str) -> str:
    lines = (text or "").strip("\n").split("\n")
    out, para, lst, lst_kind = [], [], [], None
    in_code = False
    code = []

    def inline(s):
        s = _e(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\w)", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    def flush():
        nonlocal para, lst, lst_kind
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []
        if lst:
            tag = "ol" if lst_kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in lst) + f"</{tag}>")
            lst, lst_kind = [], None

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + _e("\n".join(code)) + "</code></pre>")
                code, in_code = [], False
            else:
                flush()
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(ln)
            i += 1
            continue
        st = ln.strip()
        if not st:
            flush()
        elif st.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            head = [c.strip() for c in st.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append('<div class="tw"><table class="data"><thead><tr>' + "".join(f"<th>{inline(h)}</th>" for h in head) +
                       "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows) +
                       "</tbody></table></div>")
            continue
        elif re.match(r"^#{1,4} ", st):
            flush()
            level = min(len(st.split(" ")[0]) + 2, 5)
            out.append(f"<h{level}>{inline(st.split(' ', 1)[1])}</h{level}>")
        elif re.match(r"^[-*] ", st):
            if lst_kind not in (None, "ul"):
                flush()
            if para:
                out.append("<p>" + inline(" ".join(para)) + "</p>")
                para = []
            lst_kind = "ul"
            lst.append(st[2:])
        elif re.match(r"^\d+[.)] ", st):
            if lst_kind not in (None, "ol"):
                flush()
            if para:
                out.append("<p>" + inline(" ".join(para)) + "</p>")
                para = []
            lst_kind = "ol"
            lst.append(st.split(" ", 1)[1])
        elif st.startswith("> "):
            flush()
            out.append(f'<blockquote>{inline(st[2:])}</blockquote>')
        else:
            if lst:
                flush()
            para.append(st)
        i += 1
    if in_code:
        out.append("<pre><code>" + _e("\n".join(code)) + "</code></pre>")
    flush()
    return "\n".join(out)


class Report:
    def __init__(self, fs=None, title: str | None = None, subtitle: str | None = None, problem: str | None = None,
                 approach: str | None = None, units=None, author: str | None = None, auto: bool = True):
        self.fs = fs
        self.units = as_unitset(units) if units is not None else (fs.units if fs is not None else as_unitset("ENG"))
        self.title = title or (fs.name if fs is not None else "Refluxion report")
        self.subtitle = subtitle or ""
        self.author = author
        self.created = dt.datetime.now()
        self.highlights = []
        self.front: list[Section] = []
        self.analyses: list[Section] = []
        self.auto = auto
        self.assets: dict[str, str] = {}
        if problem:
            self.text("Problem", problem, group="front")
        if approach:
            self.text("Approach & assumptions", approach, group="front")

    # --- building blocks -------------------------------------------------------------------------
    def _add(self, section: Section):
        (self.front if section.group == "front" else self.analyses).append(section)
        return section

    def _unique_id(self, base):
        ids = {s.id for s in self.front + self.analyses}
        i, sid = 2, base
        while sid in ids:
            sid, i = f"{base}-{i}", i + 1
        return sid

    def text(self, title: str, markdown: str, group: str = "analysis"):
        """A narrative section (Markdown subset: paragraphs, lists, **bold**, `code`, tables, code blocks)."""
        self._add(Section(self._unique_id(_slug(title)), title, [("md", markdown)], group))
        return self

    def hand_calc(self, title: str, markdown: str):
        """Worked equations / hand-calculation check (rendered in a monospace card)."""
        self._add(Section(self._unique_id(_slug(title)), title, [("calc", markdown)]))
        return self

    def callout(self, text: str, kind: str = "info", title: str | None = None):
        sec = self.analyses[-1] if self.analyses else self._add(Section(self._unique_id("notes"), title or "Notes"))
        sec.items.append(("callout", kind, text))
        return self

    def highlight(self, label: str, value, kind: str | None = None, target=None, unit: str = "", quantity: str | None = None,
                  note: str = ""):
        """A key result card. ``value`` is in the report's units (or SI if ``quantity`` is given).
        With ``kind`` ('>=', '<=', '==') and ``target`` the card shows met / not met."""
        status = "neutral"
        if kind and target is not None and value is not None and np.isfinite(value):
            ok = (value >= target) if kind == ">=" else (value <= target) if kind == "<=" else abs(value - target) <= 1e-6 * max(1, abs(target))
            status = "good" if ok else "bad"
        self.highlights.append(dict(label=label, value=value, unit=unit, kind=kind, target=target, status=status,
                                    quantity=quantity, note=note))
        return self

    def table(self, title: str, columns, rows, caption: str = "", section: str | None = None, collapsible: bool = False):
        """``columns``: list of labels or (label, quantity). Cells: numbers (report units), str, Q, Pill."""
        item = ("table", dict(columns=list(columns), rows=[list(r) for r in rows], caption=caption, title=title,
                              collapsible=collapsible))
        if section:
            sec = next(s for s in self.front + self.analyses if s.title == section)
            sec.items.append(item)
        else:
            self._add(Section(self._unique_id(_slug(title)), title, [item]))
        return self

    def chart(self, title: str, series, x_label="", y_label="", caption="", section: str | None = None, **kw):
        """A line chart; ``series`` = list of dict(name=, x=, y=)."""
        name = self._unique_id(_slug(title))
        svg = line_chart(series, title="", x_label=x_label, y_label=y_label, chart_id=name, **kw)
        svg_sa = line_chart(series, title=title, x_label=x_label, y_label=y_label, standalone=True, **kw)
        item = ("svg", svg, svg_sa, name, title, caption)
        if section:
            sec = next(s for s in self.front + self.analyses if s.title == section)
            sec.items.append(item)
        else:
            self._add(Section(name, title, [item]))
        return self

    def figure(self, title: str, svg: str, caption: str = "", section: str | None = None):
        """Embed a ready-made SVG (a hand-drawn PFD, a sketch) as a figure."""
        name = self._unique_id(_slug(title))
        item = ("svg", svg, svg, name, title, caption)
        if section:
            next(s for s in self.front + self.analyses if s.title == section).items.append(item)
        else:
            self._add(Section(name, title, [item]))
        return self

    # --- flowsheet-aware sections -------------------------------------------------------------------
    def _u(self, q):
        return self.units.unit(q)

    def column(self, block: str, components=None, title: str | None = None):
        """Column profiles: temperature, liquid/vapor flows and composition vs stage."""
        self._add(self._column_section(block, components, title))
        self._column_done = getattr(self, "_column_done", set()) | {block}
        return self

    def _column_section(self, block: str, components=None, title: str | None = None) -> Section:
        b = self.fs[block]
        p = b.tables.get("profiles")
        if p is None:
            raise ValueError(f"{block} has no stage profiles (is it a solved RadFrac?)")
        u = self.units
        st = list(p["stage"])
        ids = p["components"]
        if components is None:
            spread = (np.max(p["x"], axis=0) - np.min(p["x"], axis=0))
            components = [ids[i] for i in np.argsort(-spread)[:6]]
        sid = self._unique_id(_slug(title or f"{block} profiles"))
        sec = Section(sid, title or f"{block}: column profiles")
        T = [u.from_si(t, "temperature") for t in p["T"]]
        sec.items.append(("svg", line_chart([dict(name="Temperature", x=st, y=T)], x_label="Stage",
                                            y_label=f"Temperature ({self._u('temperature')})", chart_id=f"{sid}-T", x_integer=True),
                          line_chart([dict(name="Temperature", x=st, y=T)], title=f"{block} temperature profile", x_label="Stage",
                                     y_label=f"Temperature ({self._u('temperature')})", standalone=True, x_integer=True),
                          f"{sid}-T", "Temperature profile", "Stage 1 is the top of the column."))
        flows = [dict(name="Liquid leaving stage", x=st, y=[u.from_si(v, "mole_flow") for v in p["L"]]),
                 dict(name="Vapor leaving stage", x=st, y=[u.from_si(v, "mole_flow") for v in p["V"]])]
        sec.items.append(("svg", line_chart(flows, x_label="Stage", y_label=f"Flow ({self._u('mole_flow')})", chart_id=f"{sid}-LV", x_integer=True),
                          line_chart(flows, title=f"{block} liquid and vapor flows", x_label="Stage",
                                     y_label=f"Flow ({self._u('mole_flow')})", standalone=True, x_integer=True),
                          f"{sid}-LV", "Liquid and vapor flow profiles", ""))
        for phase, key in (("Liquid", "x"), ("Vapor", "y")):
            ser = [dict(name=c, x=st, y=list(p[key][:, ids.index(c)])) for c in components]
            sec.items.append(("svg", line_chart(ser, x_label="Stage", y_label=f"{phase} mole fraction", chart_id=f"{sid}-{key}", x_integer=True),
                              line_chart(ser, title=f"{block} {phase.lower()} composition", x_label="Stage",
                                         y_label=f"{phase} mole fraction", standalone=True, x_integer=True),
                              f"{sid}-{key}", f"{phase} composition profile", ""))
        cols = [("Stage", None), ("Temperature", "temperature"), ("Pressure", "pressure"), ("Liquid from", "mole_flow"),
                ("Vapor from", "mole_flow"), ("Heat duty", "duty")] + [(f"x {c}", None) for c in components]
        rows = []
        for j in range(len(st)):
            rows.append([int(st[j]), Q(p["T"][j], "temperature"), Q(p["P"][j], "pressure"), Q(p["L"][j], "mole_flow"),
                         Q(p["V"][j], "mole_flow"), Q(p["Q"][j], "duty") if p["Q"][j] else ""] +
                        [float(p["x"][j, ids.index(c)]) for c in components])
        sec.items.append(("table", dict(columns=cols, rows=rows, caption="Stage-by-stage results", title="Stage profiles",
                                        collapsible=True)))
        return sec

    def sensitivity(self, result, x: str | None = None, y=None, title: str | None = None, x_label: str | None = None,
                    y_labels: dict | None = None, targets: dict | None = None):
        """Plot and tabulate a :func:`refluxion.tools.sensitivity` result. With two varied inputs the
        second becomes the parametric (one line per value)."""
        paths = [p for p, _ in result.vary]
        x = x or paths[0]
        y = y or result.tabulate
        y_labels = y_labels or {}
        sid = self._unique_id(_slug(title or f"sensitivity {result.name}"))
        sec = Section(sid, title or f"Sensitivity {result.name}")
        rows_ok = [r for r in result.rows if r["status"] == "ok"]
        param = [p for p in paths if p != x]
        for k, label in enumerate(y):
            if param:
                vals = sorted({r[param[0]] for r in result.rows})
                ser = []
                for v in vals:
                    pts = sorted([(r[x], r[label]) for r in rows_ok if r[param[0]] == v])
                    ser.append(dict(name=f"{param[0]} = {fmt(v)}", x=[a for a, _ in pts], y=[b for _, b in pts]))
            else:
                pts = sorted([(r[x], r[label]) for r in rows_ok])
                ser = [dict(name=label, x=[a for a, _ in pts], y=[b for _, b in pts])]
            refs = []
            if targets and label in targets:
                refs.append(dict(value=targets[label], label=f"target {fmt(targets[label])}"))
            yl = y_labels.get(label, label)
            xl = x_label or x
            sec.items.append(("svg", line_chart(ser, x_label=xl, y_label=yl, chart_id=f"{sid}-{k}", ref_lines=refs),
                              line_chart(ser, title=f"{label} vs {xl}", x_label=xl, y_label=yl, standalone=True, ref_lines=refs),
                              f"{sid}-{k}", f"{label} vs {xl}", ""))
        cols = [(p, None) for p in paths] + [(t, None) for t in result.tabulate] + [("Status", None)]
        rows = [[r[p] for p in paths] + [r[t] for t in result.tabulate] +
                [Pill("ok", "good") if r["status"] == "ok" else Pill(r["status"], "bad")] for r in result.rows]
        sec.items.append(("table", dict(columns=cols, rows=rows, caption=f"{len(result.rows)} cases in {result.seconds:.1f} s "
                                                                         "(inputs and results in the flowsheet's units)",
                                        title="Sensitivity table", collapsible=len(rows) > 12)))
        self._add(sec)
        return self

    def case_study(self, result, title: str | None = None):
        sid = self._unique_id(_slug(title or result.name))
        cols = [("Case", None), ("Changes", None)] + [(t, None) for t in result.tabulate] + [("Status", None)]
        rows = [[r["case"], "; ".join(f"{k} = {fmt(v)}" for k, v in r["changes"].items()) or "base case"] +
                [r[t] for t in result.tabulate] + [Pill("ok", "good") if r["status"] == "ok" else Pill(r["status"], "bad")]
                for r in result.rows]
        self._add(Section(sid, title or result.name, [("table", dict(columns=cols, rows=rows, caption="", title="Cases"))]))
        return self

    def optimization(self, result, title: str | None = None):
        sid = self._unique_id(_slug(title or f"optimization {result.name}"))
        sec = Section(sid, title or f"Optimization {result.name}")
        sec.items.append(("callout", "good" if result.success else "bad",
                          f"{'Converged' if result.success else 'Did not converge'}: {result.message} "
                          f"({result.evaluations} flowsheet evaluations, {result.seconds:.1f} s). "
                          f"Objective ({result.sense}imize) = {fmt(result.objective)}."))
        rows = [[k, v] for k, v in result.x.items()] + [[f"constraint: {k}", v] for k, v in result.constraints.items()]
        sec.items.append(("table", dict(columns=[("Variable", None), ("Optimum", None)], rows=rows, caption="", title="Optimum")))
        ok = [h for h in result.history if h["ok"]]
        if ok:
            ser = [dict(name="Objective", x=list(range(1, len(ok) + 1)), y=[h["objective"] for h in ok])]
            sec.items.append(("svg", line_chart(ser, x_label="Flowsheet evaluation", y_label="Objective", chart_id=f"{sid}-h", markers=False),
                              line_chart(ser, title="Objective during the search", x_label="Flowsheet evaluation", y_label="Objective",
                                         standalone=True, markers=False), f"{sid}-h", "Objective during the search", ""))
        self._add(sec)
        return self

    def pinch(self, result, title: str = "Pinch analysis"):
        Tu, CPu, Qu = result.units
        sid = self._unique_id(_slug(title))
        sec = Section(sid, title)
        sec.items.append(("callout", "info", result.summary()))
        srows = [[s.name, "hot" if s.hot else "cold", s.T_supply, s.T_target, s.CP, s.duty] for s in result.streams]
        sec.items.append(("table", dict(columns=["Stream", "Type", f"Supply T ({Tu})", f"Target T ({Tu})", f"CP ({CPu})", f"Duty ({Qu})"],
                                        rows=srows, caption=f"dTmin = {fmt(result.dTmin)} {Tu}", title="Streams")))
        rows = []
        for r in result.table_rows():
            rows.append([r["boundary"], r["T_hot"], r["T_cold"], r.get("available", ""), r.get("required", ""), r.get("net", ""),
                         r["cascade_zero"], r["cascade"]])
        sec.items.append(("table", dict(columns=[f"Boundary T ({Tu})", f"Hot T ({Tu})", f"Cold T ({Tu})", f"Hot streams release ({Qu})",
                                                 f"Cold streams need ({Qu})", f"Net surplus ({Qu})", f"Cascade from 0 ({Qu})",
                                                 f"Feasible cascade ({Qu})"],
                                        rows=rows, caption="Problem table: each interval's surplus (+) or deficit (-) is cascaded "
                                                           "downward; QH,min makes the cascade non-negative.",
                                        title="Problem table")))
        cc = [dict(name="Hot composite", x=list(result.hot_composite["H"]), y=list(result.hot_composite["T"])),
              dict(name="Cold composite", x=list(result.cold_composite["H"]), y=list(result.cold_composite["T"]))]
        sec.items.append(("svg", line_chart(cc, x_label=f"Enthalpy ({Qu})", y_label=f"Temperature ({Tu})", chart_id=f"{sid}-cc"),
                          line_chart(cc, title="Composite curves", x_label=f"Enthalpy ({Qu})", y_label=f"Temperature ({Tu})", standalone=True),
                          f"{sid}-cc", "Composite curves", "The cold curve is shifted right by QC,min; the closest vertical approach is dTmin at the pinch."))
        g = [dict(name="Grand composite", x=list(result.gcc["H"]), y=list(result.gcc["T"]))]
        sec.items.append(("svg", line_chart(g, x_label=f"Net heat flow ({Qu})", y_label=f"Shifted temperature ({Tu})", chart_id=f"{sid}-gcc"),
                          line_chart(g, title="Grand composite curve", x_label=f"Net heat flow ({Qu})", y_label=f"Shifted temperature ({Tu})",
                                     standalone=True), f"{sid}-gcc", "Grand composite curve", "Zero heat flow = pinch; the top point is QH,min, the bottom QC,min."))
        self._add(sec)
        return self

    def property_plot(self, data: dict, title: str | None = None):
        """Txy / Pxy / xy data from :mod:`refluxion.analysis`."""
        kind = data["kind"]
        c1 = data["c1"]
        if kind == "Txy":
            ser = [dict(name="Bubble point (liquid x)", x=list(data["x"]), y=list(data["T_bubble"])),
                   dict(name="Dew point (vapor y)", x=list(data["x"]), y=list(data["T_dew"]))]
            yl = f"Temperature ({data['T_unit']})"
            ttl = title or f"Txy: {c1}/{data['c2']} at {fmt(data['P'])} {data['P_unit']}"
        elif kind == "Pxy":
            ser = [dict(name="Bubble point", x=list(data["x"]), y=list(data["P_bubble"])),
                   dict(name="Dew point", x=list(data["x"]), y=list(data["P_dew"]))]
            yl = f"Pressure ({data['P_unit']})"
            ttl = title or f"Pxy: {c1}/{data['c2']} at {fmt(data['T'])} {data['T_unit']}"
        else:
            ser = [dict(name="Equilibrium", x=list(data["x"]), y=list(data["y"])), dict(name="y = x", x=[0, 1], y=[0, 1])]
            yl = f"Vapor mole fraction {c1}"
            ttl = title or f"xy: {c1}/{data['c2']}"
        return self.chart(ttl, ser, x_label=f"Mole fraction {c1}", y_label=yl, markers=False)

    # --- automatic sections -----------------------------------------------------------------------
    def _auto_sections(self) -> list[Section]:
        fs = self.fs
        if fs is None or not self.auto:
            return []
        secs = []
        # flowsheet
        info = {}
        for s in fs.streams.values():
            if s.solved:
                info[s.name] = json.dumps(dict(T=Q(s.T, "temperature").__dict__, P=Q(s.P, "pressure").__dict__,
                                               F=Q(s.F, "mole_flow").__dict__, vf=s.vf, phase=s.phase))
        items = [("pfd", flowsheet_svg(fs, stream_info=info), flowsheet_svg(fs, standalone=True), "flowsheet")]
        seq = fs.describe_sequence() if fs.blocks else ""
        loops = [f"{lp.name}: tear {', '.join(t.name for t in lp.tears)}, {lp.iterations} iterations "
                 f"({'converged' if lp.converged else 'NOT converged'})" for lp in fs.loops]
        items.append(("md", f"**Calculation sequence:** `{seq}`" + ("\n\n" + "\n".join(f"- {x}" for x in loops) if loops else "")))
        for lp in fs.loops:
            if lp.history:
                ser = [dict(name=lp.name, x=list(range(1, len(lp.history) + 1)), y=list(lp.history))]
                items.append(("svg", line_chart(ser, x_label="Iteration", y_label="max error / tolerance", log_y=True,
                                                chart_id=f"conv-{lp.name}", ref_lines=[dict(value=1.0, label="converged")], x_integer=True),
                              line_chart(ser, title=f"{lp.name} convergence", x_label="Iteration", y_label="max error / tolerance",
                                         log_y=True, standalone=True, ref_lines=[dict(value=1.0, label="converged")], x_integer=True),
                              f"conv-{_slug(lp.name)}", f"{lp.name} convergence (Wegstein)", "Tear-stream error relative to tolerance; below 1 means converged."))
        for ds in fs.design_specs:
            r = ds.result or {}
            items.append(("callout", "good" if r.get("converged") else "bad",
                          f"Design spec {ds.name}: vary `{ds.vary}` -> {fmt(r.get('value'))}; measured {fmt(r.get('measured'))} "
                          f"vs target {fmt(ds.target)} (tolerance {fmt(ds.tol)})."))
        secs.append(Section("flowsheet", "Flowsheet", items))
        # streams
        secs.append(Section("streams", "Stream results", [("table", self._stream_table())]))
        # blocks
        items = []
        for b in fs.blocks.values():
            rows = []
            for key, (label, value, qty) in b.results.items():
                if value is None:
                    continue
                if qty in ("fraction", None, ""):
                    rows.append([label, value, ""])
                else:
                    rows.append([label, Q(value, qty), ("unit", qty)])
            items.append(("block", b, rows))
        secs.append(Section("blocks", "Block results", items))
        # column profiles for every rigorous column not already added by hand
        done = getattr(self, "_column_done", set())
        for b in fs.blocks.values():
            if b.type_name == "RadFrac" and b.name not in done and "profiles" in b.tables:
                sec = self._column_section(b.name)
                sec.id = f"profiles-{_slug(b.name)}"
                secs.append(sec)
        # Aspen Plus setup guide
        from .aspen import aspen_setup_md
        secs.append(Section("aspen", "Set it up in Aspen Plus", [("md", aspen_setup_md(fs))]))
        # checks
        rows = []
        for r in fs.balance_check():
            ok = abs(r["mass"]) < 1e-6 and abs(r["energy"]) < 1e-5
            rows.append([r["block"], r["type"], f"{r['mass']:.2e}", f"{r['energy']:.2e}", Pill("closed" if ok else "check", "good" if ok else "warn")])
        items = [("table", dict(columns=["Block", "Type", "Mass balance (rel.)", "Energy balance (rel.)", "Status"], rows=rows,
                                caption="Relative imbalance (in - out) / in for each block.", title="Balances"))]
        for w in fs.warnings():
            items.append(("callout", "warn", w))
        secs.append(Section("checks", "Checks", items))
        # components & method
        comp_rows = [[c.id, c.name, c.formula, c.cas, c.MW, Q(c.Tc, "temperature"), Q(c.Pc, "pressure"), c.omega, Q(c.Tb, "temperature")]
                     for c in fs.comps]
        notes = fs.pm.info()
        md = f"**Property method:** {notes['method']} - {notes['description']}\n\n" + "\n".join(f"- {n}" for n in notes["notes"])
        md += ("\n- Pure-component data: open `chemicals`/`thermo` databanks; ideal-gas Cp, vapor pressure and heat of "
               "vaporization fitted to analytic forms. Numbers will be close to Aspen's but not identical.")
        src_rows = [[c.id, c.sources.get("cp", ""), c.sources.get("psat", ""), c.sources.get("dhvap", ""),
                     c.sources.get("liquid_volume", "")] for c in fs.comps]
        secs.append(Section("components", "Components & property method", [
            ("md", md),
            ("table", dict(columns=["ID", "Name", "Formula", "CAS", "MW", ("Tc", "temperature"), ("Pc", "pressure"), "omega",
                                    ("Normal bp", "temperature")], rows=comp_rows, caption="", title="Components", collapsible=True)),
            ("table", dict(columns=["ID", "Ideal-gas Cp", "Vapor pressure", "Heat of vaporization", "Liquid volume (Rackett)"],
                           rows=src_rows, caption="Which data-based correlation each property came from, and how closely the fitted "
                                                  "form reproduces it (Aspen's equivalents: CPIG, PLXANT, DHVLDP, RKTZRA).",
                           title="Pure-component data sources", collapsible=True))]))
        secs.append(Section("log", "Run log", [("log", "\n".join(fs.log))]))
        return secs

    def _stream_table(self):
        fs = self.fs
        streams = [s for s in fs.streams.values() if s.solved]
        cols = [("", None)] + [(s.name, None) for s in streams]
        rows = [["From"] + [s.source[0].name if s.source else "feed" for s in streams],
                ["To"] + [s.dest[0].name if s.dest else "product" for s in streams],
                ["Phase"] + [s.phase for s in streams],
                [("Temperature", "temperature")] + [Q(s.T, "temperature") for s in streams],
                [("Pressure", "pressure")] + [Q(s.P, "pressure") for s in streams],
                ["Vapor fraction"] + [s.vf for s in streams],
                [("Mole flow", "mole_flow")] + [Q(s.F, "mole_flow") for s in streams],
                [("Mass flow", "mass_flow")] + [Q(s.mass_flow, "mass_flow") for s in streams],
                [("Enthalpy flow", "duty")] + [Q(s.H, "duty") for s in streams],
                [("Volume flow", "volume_flow")] + [Q(s.volume_flow, "volume_flow") for s in streams],
                [("__head__", "Component mole flows", "mole_flow")]]
        for i, c in enumerate(fs.comps.ids):
            rows.append([("  " + c, "mole_flow")] + [Q(s.flows[i], "mole_flow") for s in streams])
        rows.append([("__head__", "Mole fractions", None)])
        for i, c in enumerate(fs.comps.ids):
            rows.append(["  " + c] + [float(s.z[i]) for s in streams])
        return dict(columns=cols, rows=rows, caption="Like Aspen's stream summary. Hover a stream on the flowsheet for a quick look.",
                    title="Streams", stream_table=True)

    # --- rendering -----------------------------------------------------------------------------------
    def sections(self) -> list[Section]:
        auto = self._auto_sections()
        head = [s for s in auto if s.id in ("flowsheet", "streams", "blocks") or s.id.startswith("profiles-")]
        tail = [s for s in auto if s not in head]
        return self.front + head + self.analyses + tail

    def to_html(self) -> str:
        from .page import render_html
        return render_html(self)

    def to_markdown(self, asset_dir: str = "assets") -> tuple[str, dict]:
        from .markdown import render_markdown
        return render_markdown(self, asset_dir)

    def save(self, path) -> dict:
        """Write ``<path>.html``, ``<path>.md`` and ``<path>_assets/*.svg``. Returns the file paths."""
        p = Path(path)
        if p.suffix in (".html", ".md"):
            p = p.with_suffix("")
        p.parent.mkdir(parents=True, exist_ok=True)
        html_path = p.with_suffix(".html")
        md_path = p.with_suffix(".md")
        asset_dir = p.parent / f"{p.name}_assets"
        html_path.write_text(self.to_html(), encoding="utf-8")
        md, assets = self.to_markdown(f"{p.name}_assets")
        asset_dir.mkdir(exist_ok=True)
        for name, svg in assets.items():
            (asset_dir / name).write_text(svg, encoding="utf-8")
        md_path.write_text(md, encoding="utf-8")
        return dict(html=str(html_path), markdown=str(md_path), assets=str(asset_dir))


def unit_tables_json() -> str:
    """Conversion data for the HTML unit switcher."""
    sets = {k: v for k, v in _BASE_SETS.items()}
    return json.dumps(dict(factors=FACTORS, sets=sets))


__all__ = ["Report", "Q", "Pill", "fmt", "md_to_html"]
