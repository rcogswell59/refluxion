"""Render a :class:`Report` as Markdown (GitHub-flavored) with SVG figure files."""

from __future__ import annotations

import re

import numpy as np

from .blocks import block_extras


def _cell(c, units):
    from . import Pill, Q, fmt

    if isinstance(c, Q):
        if c.si is None:
            return "-"
        return fmt(units.from_si(c.si, c.quantity) if c.quantity != "fraction" else c.si)
    if isinstance(c, Pill):
        mark = {"good": "OK", "bad": "FAIL", "warn": "CHECK"}.get(c.status, "")
        return f"{mark} {c.text}".strip() if mark and mark.lower() != c.text.lower() else c.text
    if isinstance(c, tuple) and len(c) == 2 and c[0] == "unit":
        return units.unit(c[1])
    if isinstance(c, tuple) and len(c) == 2:
        label, q = c
        return f"{label} ({units.unit(q)})" if q else str(label)
    if isinstance(c, (int, np.integer)) and not isinstance(c, bool):
        return str(c)
    if isinstance(c, (float, np.floating)):
        return fmt(float(c))
    return str(c).replace("|", "\\|")


def _table(t, units) -> str:
    cols = []
    for col in t["columns"]:
        if isinstance(col, tuple):
            label, q = col
            cols.append(f"{label} ({units.unit(q)})" if q else str(label))
        else:
            cols.append(str(col))
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|"]
    for row in t["rows"]:
        if row and isinstance(row[0], tuple) and len(row[0]) == 3 and row[0][0] == "__head__":
            _, label, q = row[0]
            lines.append(f"| **{label}{' (' + units.unit(q) + ')' if q else ''}** |" + " |" * (len(cols) - 1))
            continue
        lines.append("| " + " | ".join(_cell(c, units) for c in row) + " |")
    out = "\n".join(lines)
    if t.get("caption"):
        out += f"\n\n*{t['caption']}*"
    return out


def _asset(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") + ".svg"


def render_markdown(rep, asset_dir: str):
    from .. import __version__
    from . import fmt

    fs = rep.fs
    units = rep.units
    assets = {}
    out = [f"# {rep.title}", ""]
    if rep.subtitle:
        out += [rep.subtitle, ""]
    meta = []
    if fs is not None:
        meta.append(f"**Status:** {fs.status}")
        meta.append(f"**Property method:** {fs.pm.name}")
        meta.append(f"**Units:** {units.base}")
        meta.append(f"**Solved in:** {fs.run_time:.2f} s")
    meta.append(f"**Generated:** {rep.created.strftime('%b %d, %Y %H:%M')} with Refluxion {__version__}")
    out += [" &middot; ".join(meta), "", "> Open the `.html` report for interactive charts, the clickable flowsheet and the unit switcher.", ""]
    if rep.highlights:
        out += ["## Key results", "", "| Result | Value | Target | Status |", "|---|---:|---|---|"]
        for h in rep.highlights:
            v = h["value"]
            if h["quantity"] and v is not None:
                vs = f"{fmt(units.from_si(v, h['quantity']))} {units.unit(h['quantity'])}"
            else:
                vs = f"{fmt(v)} {h['unit']}".strip()
            tgt = f"{h['kind']} {fmt(h['target'])} {h['unit']}".strip() if h["kind"] else (h["note"] or "")
            st = {"good": "meets", "bad": "**misses**"}.get(h["status"], "")
            out.append(f"| {h['label']} | {vs} | {tgt} | {st} |")
        out.append("")
    for n, s in enumerate(rep.sections(), 1):
        out += [f"## {n}. {s.title}", ""]
        for k, it in enumerate(s.items):
            out += _item(rep, it, s.id, k, asset_dir, assets)
            out.append("")
    return "\n".join(out).rstrip() + "\n", assets


def _item(rep, it, sid, k, asset_dir, assets) -> list[str]:
    from . import Q, fmt

    units = rep.units
    kind = it[0]
    if kind == "md":
        return [it[1].strip()]
    if kind == "calc":
        return [it[1].strip()]
    if kind == "callout":
        _, level, text = it
        tag = {"good": "OK", "bad": "Warning", "warn": "Check", "info": "Note"}.get(level, "Note")
        return [f"> **{tag}:** {text.strip()}"]
    if kind == "table":
        return [_table(it[1], units)]
    if kind == "svg":
        _, _svg, standalone, name, title, caption = it
        fn = _asset(f"{sid}-{name}")
        assets[fn] = standalone
        return [f"![{title}]({asset_dir}/{fn})", f"*{title}{' - ' + caption if caption else ''}*"]
    if kind == "pfd":
        fn = _asset("flowsheet")
        assets[fn] = it[2]
        return [f"![Flowsheet]({asset_dir}/{fn})", "*Process flow diagram (dashed = tear stream).*"]
    if kind == "block":
        _, b, rows = it
        lines = [f"### {b.name} ({b.type_name}) - {b.status}", "", f"`{b.describe_spec()}`", ""]
        for m in b.messages:
            lines += [f"> **Check:** {m}", ""]
        lines += ["| Result | Value | Units |", "|---|---:|---|"]
        for label, val, unit in rows:
            if isinstance(val, Q):
                lines.append(f"| {label} | {fmt(units.from_si(val.si, val.quantity))} | {units.unit(val.quantity)} |")
            else:
                lines.append(f"| {label} | {fmt(val)} | |")
        for j, ex in enumerate(block_extras(rep, b)):
            lines.append("")
            lines += _item(rep, ex, f"{sid}-{b.name}", j, asset_dir, assets)
        return lines
    if kind == "log":
        return ["<details><summary>Run log</summary>", "", "```text", it[1], "```", "", "</details>"]
    return []
