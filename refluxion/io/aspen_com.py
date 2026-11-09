"""Drive Aspen Plus V15 through its COM interface: open a .bkp, run it, read the variable tree, save a .bkp.

Needs Windows, Aspen Plus V15 (``Apwn.Document.41.0``), a reachable license and ``pywin32``.

    with Aspen() as a:
        a.open("M1_design.bkp")
        a.run()
        print(a.status())
        print(a.value(r"\\Data\\Streams\\ACN\\Output\\MASSFRAC\\MIXED\\ACN"))
        a.save_bkp("M1_design_run.bkp")

Paths are Aspen's variable-explorer paths (Customize › Variable Explorer). ``dump`` prints a subtree, which is how
new paths are found.
"""

from __future__ import annotations

from pathlib import Path

PROGID = "Apwn.Document.41.0"          # Aspen Plus V15


class AspenError(RuntimeError):
    pass


class Aspen:
    def __init__(self, visible: bool = False, progid: str = PROGID):
        import win32com.client
        try:
            self.app = win32com.client.DispatchEx(progid)
        except Exception as e:                       # no Aspen, or no license at start-up
            raise AspenError(f"could not start {progid}: {e}") from e
        self.visible = visible          # Aspen accepts Visible / SuppressDialogs only once a file is open
        self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self, path):
        self.path = Path(path).resolve()
        if not self.path.exists():
            raise AspenError(f"{self.path} not found")
        self.app.InitFromArchive2(str(self.path))
        self.app.Visible = 1 if self.visible else 0
        self.app.SuppressDialogs = 1

    def run(self, reinit: bool = True):
        if reinit:
            self.app.Reinit()
        self.app.Engine.Run2()

    # ---------- tree ----------
    def node(self, path: str):
        n = self.app.Tree.FindNode(path)
        if n is None:
            raise AspenError(f"no node {path}")
        return n

    def value(self, path: str, default=None):
        n = self.app.Tree.FindNode(path)
        return default if n is None else n.Value

    def set(self, path: str, v):
        self.node(path).Value = v

    def children(self, path: str) -> list[str]:
        n = self.app.Tree.FindNode(path)
        if n is None:
            return []
        try:
            return [e.Name for e in n.Elements]
        except Exception:
            return []

    def dump(self, path: str, depth: int = 1, indent: str = "") -> list[str]:
        out = []
        for name in self.children(path):
            p = f"{path}\\{name}"
            v = self.value(p)
            out.append(f"{indent}{name}" + ("" if v is None else f" = {v!r}"))
            if depth > 1:
                out += self.dump(p, depth - 1, indent + "  ")
        return out

    # ---------- results ----------
    def status(self) -> dict:
        """Run status and the Control Panel messages Aspen stores in the tree."""
        rs = r"\Data\Results Summary\Run-Status"
        out = {"ok": None, "messages": []}
        n = self.app.Tree.FindNode(rs)
        if n is not None:
            try:
                out["compstatus"] = int(n.AttributeValue(12))     # HAP_COMPSTATUS bit flags
            except Exception:
                pass
        for leaf in ("PER_ERROR", "PER_WARNING"):
            for name in self.children(f"{rs}\\Output\\{leaf}"):
                v = self.value(f"{rs}\\Output\\{leaf}\\{name}")
                if v:
                    out["messages"].append(f"{leaf[4:]}: {v}")
        for k in ("UOSSTAT2", "RUNSTATUS", "CALCSTAT"):
            v = self.value(f"{rs}\\Output\\{k}")
            if v is not None:
                out[k] = v
        out["ok"] = not any(m.startswith("ERROR") for m in out["messages"])
        return out

    def save_bkp(self, path):
        """Save as an Aspen backup file. Aspen picks the format from the extension."""
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        self.app.SaveAs(str(p))
        if not p.exists() or not p.read_bytes()[:3] == b'MM ':
            raise AspenError(f"Aspen did not write a backup file at {p}")
        return p

    def close(self):
        try:
            self.app.Close()
        except Exception:
            pass
        try:
            self.app.Quit()
        except Exception:
            pass
