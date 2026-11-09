"""Flowsheet drawing engine: text format, router rules and the audit (no Aspen needed)."""

from refluxion.io.pfs_layout import (Router, _crosses, arrow_rect, box_h, check, label_w, parse_stream, set_zoom, stream_object,
                                     stub_len, text_rect)

HEAD = "x\nPFSVData\n# of PFS Objects = {n}\nSIZE 0 10 -10 0\n"
TAIL = "# of PFS Objects = 0\nVIEWPORT 0 10 0 -10\n"


def block(name, x, y, icon="GEN-HT"):
    return (f"BLOCK\nID: {name}\nVersion: 1\nICON: \"{icon}\"\nFlag 0\nSection GLOBAL\nAt {x:.6f} {y:.6f}\n"
            f"Label At 0.000000 0.400000\nScale 1.000000 Modifier 0\n")


def drawing(objs):
    return HEAD.format(n=len(objs)) + "".join(objs) + TAIL


def test_stream_object_round_trips_routes_labels_and_attach():
    raw = stream_object("S1", 0, (1.0, -1.0), (0.0, 0.0), [(1.0, -1.0), (0.4, -1.0), (0.2, -1.0, "r")],
                        [(1.0, -1.0), (1.6, -1.0), (1.8, -1.0, "l")], attach1=(0.0, -0.2))
    st = parse_stream(raw)
    assert st["term"] == 0 and st["at"] == (1.0, -1.0)
    assert st["routes"][0] == ([(1.0, -1.0), (0.4, -1.0), (0.2, -1.0)], "r")
    assert st["routes"][1][1] == "l"
    assert "$ D 0.000000 -0.200000\n$ C 0.000000 0.0" in raw     # multi-stream port record, as Aspen writes it
    assert raw.count("$ C 1.000000 0.0") == 1


def test_feed_arrow_direction_letter_follows_first_move():
    raw = stream_object("F", 1, (2.0, -3.0), (0.0, 0.4), None, [(2.0, -3.0), (2.0, -2.45), (2.0, -2.25, "d")])
    assert "r u 2.000000 -3.000000" in raw


def test_label_size_is_fixed_on_screen_so_it_shrinks_in_drawing_units_as_zoom_grows():
    set_zoom(1.0)
    w1 = label_w("RECYCLE1")
    assert abs(w1 * 55.2 - 131.6) < 0.5                    # measured: 132 px wide at 180 % and at 321 %
    set_zoom(2.0)
    assert abs(label_w("RECYCLE1") - w1 / 2) < 1e-9
    set_zoom(1.6)


def test_stub_has_room_for_arrow_label_and_visible_line():
    assert stub_len("WASTE-P", "r") > 0.2 + label_w("WASTE-P") + 0.2
    assert stub_len("MEKA", "d") >= 0.2 + box_h() + 0.2


def test_router_never_overlaps_and_prefers_going_around_to_crossing():
    r = Router((0, -5, 10, 0), [], [], [])
    r.add_path([(0.0, -2.5), (10.0, -2.5)], "wall")       # wall across the whole area: crossing it once is the only way
    path = r.route((5.0, -4.0), "u", (5.0, -1.0), "u", "s")
    assert path is not None
    segs = list(zip(path, path[1:]))
    assert sum(_crosses(a, b, (0.0, -2.5), (10.0, -2.5)) for a, b in segs) == 1
    assert not any(abs(a[1] + 2.5) < 0.09 and abs(b[1] + 2.5) < 0.09 for a, b in segs)   # never runs along it
    r2 = Router((0, -5, 10, 0), [], [], [])
    r2.add_path([(4.0, -2.5), (6.0, -2.5)], "short")       # short line: going around is cheaper than crossing
    path2 = r2.route((5.0, -4.0), "u", (5.0, -1.0), "u", "s")
    assert not any(_crosses(a, b, (4.0, -2.5), (6.0, -2.5)) for a, b in zip(path2, path2[1:]))


def test_router_keeps_out_of_blocks():
    r = Router((0, -5, 10, 0), [(4.0, -3.0, 6.0, -2.0)], [], [])
    path = r.route((2.0, -2.5), "r", (8.0, -2.5), "r", "s")
    assert all(not (4.0 < x < 6.0 and -3.0 < y < -2.0) for x, y in path)


def test_check_flags_crossing_label_on_arrow_and_short_gap():
    net = {"H": ("A", "B"), "V": (None, "C"), "P": ("C", None)}
    objs = [block("A", 1, -5), block("B", 9, -5), block("C", 5, -2)]
    # H runs left to right at y -5; V comes up from below and crosses it into C
    objs.append(stream_object("H", 0, (5.0, -5.0), (0, 0), [(5.0, -5.0), (1.4, -5.0, "r")], [(5.0, -5.0), (8.6, -5.0, "l")]))
    objs.append(stream_object("V", 1, (5.2, -7.0), (0, 0.35), None, [(5.2, -7.0), (5.2, -2.25, "d")]))
    # P: product whose ID box is pulled onto its own arrow
    objs.append(stream_object("P", 2, (6.5, -2.0), (-0.1, 0.0), [(6.5, -2.0), (5.4, -2.0, "r")], None))
    issues = check(drawing(objs), net)
    assert any(i.startswith("CROSS H x V") or i.startswith("CROSS V x H") for i in issues)
    assert any(i.startswith("LABEL-ON-ARROW s:P / P") for i in issues)


def test_check_flags_internal_label_without_line_on_both_sides():
    net = {"T": ("A", "B")}
    objs = [block("A", 1, -5), block("B", 2.4, -5)]
    # 0.6 of line between the ports, label 0.45 wide: no visible line beside the box
    objs.append(stream_object("TOL1", 0, (1.7, -5.0), (0, 0), [(1.7, -5.0), (1.4, -5.0, "r")], [(1.7, -5.0), (2.0, -5.0, "l")]))
    issues = check(drawing(objs), {"TOL1": ("A", "B")})
    assert "LABEL-GAP TOL1" in issues


def test_geometry_helpers():
    assert text_rect(0, 0, "AB")[2] > 0
    a = arrow_rect((0.0, 0.0), (1.0, 0.0))
    assert a == (0.0, -0.1, 0.2, 0.1)
