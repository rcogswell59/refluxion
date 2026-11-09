"""Edit an Aspen Plus backup (.bkp) as text: change block inputs, add blocks, feed streams, design specs and their
flowsheet icons. Aspen itself then opens, runs and re-saves the file (``refluxion.io.aspen_com``), so a mistake here
shows up as an Aspen input error, not as a silently wrong model.

Layout of a V15 .bkp:

* header: the model library, then a count and one 5-line record per block (``>VERSION 0`` / id / model / ``Built-In``
  / TYPE)
* input: keyword paragraphs, ``? BLOCK HEATX COND1 ? ; "METCBAR_MOLE" ; ; "GEN-HT" ; \\ PARAM ... \\ \\ FEEDS ...``.
  Sentences are separated by ``\\ \\``. Line breaks fall between tokens and mean nothing.
* ``GRAPHICS_BACKUP`` ... ``PFSVData``: one BLOCK or STREAM object per icon, each with ``At x y``
* results: ``DSET`` records, left alone (Aspen rewrites them on the next save)

Every pattern here allows any whitespace between tokens. Each edit must match exactly once, or it raises.

    b = Bkp.read("my_case.bkp")
    b.sub('BLOCK RADFRAC "COLUMN-1"', r"NSTAGE = \\d+", "NSTAGE = 25")
    b.write("M1_design.bkp")
"""

from __future__ import annotations

import re
from pathlib import Path

SEP = " \\ \\ "          # between sentences
WIDTH = 78


def ws(phrase: str) -> str:
    """Regex for a literal token phrase, any whitespace between tokens."""
    return r"\s+".join(re.escape(t) for t in phrase.split())


def q(name: str) -> str:
    """Aspen quotes ids that are not plain letters and digits (``"FEED-1"``, ``CW1O``)."""
    return name if re.fullmatch(r"[A-Z0-9]+", name) else f'"{name}"'


def wrap(text: str, width: int = WIDTH) -> str:
    """Break a one-line paragraph into lines of at most ``width`` characters, never inside a quoted string."""
    toks = re.findall(r'"[^"]*"|\S+', text)
    lines, cur = [], ""
    for t in toks:
        if cur and len(cur) + 1 + len(t) > width:
            lines.append(cur + " ")
            cur = t
        else:
            cur = f"{cur} {t}" if cur else t
    lines.append(cur + " ")
    return "\n".join(lines)


def num(v: float) -> str:
    """Aspen-style number: ``5.``, ``0.25``, ``8E-05``."""
    s = f"{v:.6g}".upper()
    return s if any(c in s for c in ".E") else s + "."


class EditError(RuntimeError):
    pass


class Bkp:
    def __init__(self, text: str):
        self.text = text

    @classmethod
    def read(cls, path) -> "Bkp":
        return cls(Path(path).read_bytes().decode("latin-1").replace("\r\n", "\n"))     # Aspen writes CRLF

    def write(self, path):
        Path(path).write_bytes(self.reflowed().replace("\n", "\r\n").encode("latin-1"))

    def reflowed(self) -> str:
        """Input lines back to Aspen's 80-column records (edits join wrapped lines)."""
        end = self._input_end()
        lines = [wrap(ln.rstrip(" ")) if len(ln) > WIDTH + 2 else ln for ln in self.text[:end].split("\n")]
        return "\n".join(lines) + self.text[end:]

    # ---------- input paragraphs ----------
    def _input_end(self) -> int:
        i = self.text.find("GRAPHICS_BACKUP")
        if i < 0:
            raise EditError("no GRAPHICS_BACKUP section")
        return i

    def _headers(self):
        """(open, close, header) for every ``? ... ?`` paragraph header in the input part."""
        end, out, marks = self._input_end(), [], []
        for m in re.finditer(r'"[^"]*"|\?', self.text[:end]):
            if m.group() == "?":
                marks.append(m.start())
        for a, b in zip(marks[0::2], marks[1::2]):
            out.append((a, b, " ".join(self.text[a + 1:b].split())))
        return out

    def para(self, header: str) -> tuple[int, int]:
        """(start, end) of a paragraph body, from just after its header to the next header."""
        want = " ".join(header.split())
        hs = self._headers()
        hits = [i for i, h in enumerate(hs) if h[2] == want]
        if len(hits) != 1:
            raise EditError(f"paragraph {header!r}: found {len(hits)}")
        i = hits[0]
        end = hs[i + 1][0] if i + 1 < len(hs) else self._input_end()
        return hs[i][1] + 1, end

    def body(self, header: str) -> str:
        a, b = self.para(header)
        return self.text[a:b]

    def has(self, header: str) -> bool:
        want = " ".join(header.split())
        return any(h[2] == want for h in self._headers())

    def sub(self, header: str, pattern: str, repl: str, count: int = 1):
        """Replace a regex inside one paragraph; it must match exactly ``count`` times."""
        a, b = self.para(header)
        seg = self.text[a:b]
        found = len(re.findall(pattern, seg, flags=re.S))
        if found != count:
            raise EditError(f"{header}: {pattern!r} matched {found} times, expected {count}")
        seg = re.sub(pattern, lambda m: repl, seg, flags=re.S)
        self.text = self.text[:a] + seg + self.text[b:]

    def insert_before(self, header: str, pattern: str, text: str):
        """Insert ``text`` (one or more sentences) in front of the sentence that starts with ``pattern``."""
        a, b = self.para(header)
        m = list(re.finditer(pattern, self.text[a:b], flags=re.S))
        if len(m) != 1:
            raise EditError(f"{header}: anchor {pattern!r} matched {len(m)} times")
        i = a + m[0].start()
        self.text = self.text[:i] + "\n" + wrap(text + " " + SEP.strip()) + "\n" + self.text[i:]

    def add_paragraph(self, text: str, after: str):
        """Add a whole paragraph (starting with ``? ... ?``) right after the paragraph ``after``."""
        _, b = self.para(after)
        self.text = self.text[:b].rstrip(" ") + "\n" + wrap(text) + "\n" + self.text[b:]

    # ---------- header block list ----------
    def add_library_block(self, block: str, model: str, typ: str):
        m = re.search(r"endlibrary\s*\n;\s*\n(\d+)\n", self.text)
        if not m:
            raise EditError("block list not found")
        end = self.text.index("?", m.end())          # the records run up to the first input paragraph
        rec = f">VERSION 0\n{block}\n{model}\nBuilt-In\n{typ}\n"
        self.text = self.text[:m.start(1)] + str(int(m.group(1)) + 1) + self.text[m.end(1):end] + rec + self.text[end:]

    # ---------- flowsheet connectivity ----------
    def add_flowsheet_block(self, block: str, typ: str, model: str, ins, outs):
        """ins / outs: [(stream, port code)], port codes as Aspen writes them (HeatX: hot M0-1, cold M1-2)."""
        too_long = [n for n in [block] + [s for s, _ in list(ins) + list(outs)] if len(n) > 8]
        if too_long:
            # the engine accepts longer quoted IDs, but the Aspen GUI garbles their drawing and stops routing the file
            raise ValueError(f"Aspen IDs are limited to 8 characters: {too_long}")

        def grp(lst):
            return "( " + " ".join(f"{q(s)} {p}" for s, p in lst) + " )"
        text = f'BLOCK BLKID = {q(block)} BLKTYPE = "{typ}" MDLTYPE = "{model}" IN = {grp(ins)} OUT = {grp(outs)}'
        self.insert_before("FLOWSHEET GLOBAL", ws('"DEF-STREAM"'), text)

    def remove_paragraph(self, header: str):
        a, b = self.para(header)
        start = self.text.rindex("?", 0, a - 1)          # the header's opening "?"
        self.text = self.text[:start] + self.text[b:]

    def set_ports(self, block: str, ins, outs):
        """Replace a flowsheet block's IN and OUT lists: [(stream, port code)]."""
        def grp(lst):
            return "( " + " ".join(f"{q(s)} {p}" for s, p in lst) + " )"
        pat = r"(BLOCK\s+BLKID\s+=\s+" + re.escape(q(block)) + r"\s+BLKTYPE\s+=\s+\S+\s+MDLTYPE\s+=\s+\S+\s+)IN\s+=\s+\(.*?\)\s+OUT\s+=\s+\(.*?\)"
        a, b = self.para("FLOWSHEET GLOBAL")
        seg = self.text[a:b]
        m = list(re.finditer(pat, seg, flags=re.S))
        if len(m) != 1:
            raise EditError(f"flowsheet block {block}: found {len(m)}")
        seg = seg[:m[0].start()] + m[0].group(1) + f"IN = {grp(ins)} OUT = {grp(outs)}" + seg[m[0].end():]
        self.text = self.text[:a] + seg + self.text[b:]

    def remove_icon(self, oid: str):
        a, b = self._pfs()
        m = re.compile(r"^(?:STREAM|BLOCK)\nID: " + re.escape(oid) + r"\n.*?(?=^(?:STREAM|BLOCK|LEGEND)\n|\Z)",
                       flags=re.M | re.S).search(self.text, a, b)
        if not m:
            raise EditError(f"icon {oid} not found")
        self.text = self.text[:m.start()] + self.text[m.end():]
        self._pfs_count(-1)

    def add_outlet(self, block: str, stream: str, port: str):
        pat = r"(BLOCK\s+BLKID\s+=\s+" + re.escape(q(block)) + r"\s+.*?OUT\s+=\s+\(.*?)\s*\)"
        a, b = self.para("FLOWSHEET GLOBAL")
        seg = self.text[a:b]
        m = list(re.finditer(pat, seg, flags=re.S))
        if len(m) != 1:
            raise EditError(f"flowsheet block {block}: found {len(m)}")
        i = a + m[0].end(1)
        self.text = self.text[:i] + f" {q(stream)} {port}" + self.text[i:]

    # ---------- flowsheet icons (PFS) ----------
    def _pfs(self) -> tuple[int, int]:
        """(start, end) of the main flowsheet's PFS objects."""
        m = re.search(r"PFSVData\s*\n# of PFS Objects = (\d+)\s*\n", self.text)
        if not m:
            raise EditError("PFSVData not found")
        e = self.text.find("# of PFS Objects", m.end())
        return m.start(), (e if e > 0 else len(self.text))

    def _pfs_count(self, add: int):
        self.text = re.sub(r"(PFSVData\s*\n# of PFS Objects = )(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + add),
                           self.text, count=1)

    def pfs_ids(self) -> list[str]:
        a, b = self._pfs()
        return re.findall(r"^ID: (\S+)", self.text[a:b], flags=re.M)

    def _pfs_insert(self, obj: str):
        a, _ = self._pfs()
        m = re.compile(r"^SIZE .*\n", flags=re.M).search(self.text, a)
        self.text = self.text[:m.end()] + obj + self.text[m.end():]
        self._pfs_count(1)

    def add_icon(self, block: str, x: float, y: float, icon: str = "GEN-HT", section: str = "GLOBAL"):
        self._pfs_insert(f'BLOCK\nID: {block}\nVersion: 1\nICON: "{icon}"\nFlag 0\nSection {section}\n'
                         f"At {x:.6f} {y:.6f}\nLabel At 0.000000 0.300000\nScale 1.000000 Modifier 0\n")
        if section:                     # design specs and calculators have no place on the drawing
            self.grow_size(x, y)

    def add_stream_icon(self, stream: str, x: float, y: float, terminal: int):
        """terminal: 1 feed, 2 product, 0 between two blocks. Routes are left empty for Aspen to draw."""
        self._pfs_insert(f"STREAM\nID: {stream}\nVersion: 2\nFlag 0\nGLOBAL 117\nTYPE 0 TERMINAL {terminal}\n"
                         f"At {x:.6f} {y:.6f}\nLabel At 0.000000 0.000000\n" + _EMPTY_ROUTES)
        self.grow_size(x, y)

    def reset_stream_icon(self, stream: str, terminal: int):
        """A product stream that now feeds a new block: mark it as a connection and clear its drawn route."""
        a, b = self._pfs()
        m = re.compile(r"^STREAM\nID: " + re.escape(stream) + r"\n.*?(?=^(?:STREAM|BLOCK)\n|\Z)", flags=re.M | re.S)
        hit = m.search(self.text, a, b)
        if not hit:
            raise EditError(f"stream icon {stream} not found")
        obj = hit.group()
        obj = re.sub(r"TYPE 0 TERMINAL \d", f"TYPE 0 TERMINAL {terminal}", obj)
        head = obj[:obj.index("ROUTE 0 0")]
        self.text = self.text[:hit.start()] + head + _EMPTY_ROUTES + self.text[hit.end():]

    def icon_at(self, oid: str) -> tuple[float, float]:
        a, b = self._pfs()
        m = re.search(r"^ID: " + re.escape(oid) + r"\n(?:.*\n)*?At (\S+) (\S+)", self.text[a:b], flags=re.M)
        if not m:
            raise EditError(f"icon {oid} not found")
        return float(m.group(1)), float(m.group(2))

    def move_icon(self, oid: str, x: float, y: float):
        a, b = self._pfs()
        pat = re.compile(r"(^ID: " + re.escape(oid) + r"\n(?:.*\n)*?)At \S+ \S+", flags=re.M)
        m = pat.search(self.text, a, b)
        if not m:
            raise EditError(f"icon {oid} not found")
        self.text = self.text[:m.start()] + m.group(1) + f"At {x:.6f} {y:.6f}" + self.text[m.end():]
        self.grow_size(x, y)

    def grow_size(self, x: float, y: float, pad: float = 0.5):
        a, _ = self._pfs()
        m = re.compile(r"^SIZE (\S+) (\S+) (\S+) (\S+)$", flags=re.M).search(self.text, a)
        x0, x1, y0, y1 = map(float, m.groups())
        x0, x1, y0, y1 = min(x0, x - pad), max(x1, x + pad), min(y0, y - pad), max(y1, y + pad)
        self.text = self.text[:m.start()] + f"SIZE {x0:g} {x1:g} {y0:g} {y1:g}" + self.text[m.end():]


_EMPTY_ROUTES = "".join(f"{'At 0.000000 0.000000' + chr(10) + 'Label At 0.000000 0.000000' + chr(10) if k else ''}"
                        f"ROUTE 0 {k}\n$ $ 0.0 0.0\nROUTE 1 {k}\n$ $ 0.0 0.0\n" for k in range(3))
