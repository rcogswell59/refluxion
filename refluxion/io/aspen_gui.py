"""Aspen Plus GUI helpers for drawing checks (Windows, pywin32 + Pillow, Aspen license reachable).

Every helper opens its *own* Aspen instance, never touches other Aspen windows, and never takes keyboard focus.
"""

from __future__ import annotations

import ctypes
import os
import time

PROGID = "Apwn.Document.41.0"

try:            # physical pixels for window captures; must be set before this process touches any window
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def desktop_unlocked() -> bool:
    """False while the workstation is locked (or on the secure desktop)."""
    h = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)      # DESKTOP_SWITCHDESKTOP
    if not h:
        return False
    ok = bool(ctypes.windll.user32.SwitchDesktop(h))
    ctypes.windll.user32.CloseDesktop(h)
    return ok


def wait_until_unlocked(log=print, poll_s: float = 30):
    """The Aspen GUI only draws (and so only validates and re-routes streams) on an unlocked desktop."""
    told = False
    while not desktop_unlocked():
        if not told:
            log("desktop is locked: waiting (Aspen does not draw flowsheets while the screen is locked)")
            told = True
        time.sleep(poll_s)


def _open(path: str, wait_s: float):
    import win32com.client as w

    wait_until_unlocked()

    app = w.DispatchEx(PROGID)
    app.InitFromArchive2(os.path.abspath(path))
    app.Visible = 1            # the flowsheet is only drawn (and routes only validated) when the GUI is visible
    time.sleep(wait_s)
    return app


def _close(app):
    for f in ("Close", "Quit"):
        try:
            getattr(app, f)()
        except Exception:
            pass


def roundtrip(src: str, dst: str, wait_s: float = 30) -> None:
    """Open ``src`` in the GUI, let Aspen draw (and silently re-route anything it rejects), save as ``dst``."""
    app = _open(src, wait_s)
    try:
        app.SaveAs(os.path.abspath(dst))
    finally:
        _close(app)


def screenshot(src: str, png: str, wait_s: float = 30, size=(2400, 1500)) -> None:
    """Open ``src``, resize its window without activating it, capture it with PrintWindow (works behind other windows).

    The visible area is the file's ``VIEWPORT`` line, so write it to fit the drawing first.
    """
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    app = _open(src, wait_s)
    try:
        name = os.path.basename(src)
        hwnds = []
        win32gui.EnumWindows(lambda h, acc: acc.append(h) if name in win32gui.GetWindowText(h)
                             and win32gui.IsWindowVisible(h) else None, hwnds)
        if not hwnds:
            raise RuntimeError(f"no Aspen window titled with {name}")
        h = hwnds[0]
        win32gui.SetWindowPos(h, 0, 0, 0, size[0], size[1], win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
        time.sleep(6)
        left, top, right, bottom = win32gui.GetWindowRect(h)
        wdt, hgt = right - left, bottom - top
        hdc = win32gui.GetWindowDC(h)
        mdc = win32ui.CreateDCFromHandle(hdc)
        sdc = mdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mdc, wdt, hgt)
        sdc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(h, sdc.GetSafeHdc(), 2)
        info, bits = bmp.GetInfo(), bmp.GetBitmapBits(True)
        Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1).save(png)
        win32gui.DeleteObject(bmp.GetHandle())
        sdc.DeleteDC()
        mdc.DeleteDC()
        win32gui.ReleaseDC(h, hdc)
    finally:
        _close(app)
