"""Design report sections (equipment, utilities, economics, stream table) and the AICHE unit set."""

from __future__ import annotations

from refluxion import Flowsheet, RadFrac
from refluxion.design.plant import PlantBasis, evaluate_plant
from refluxion.report.design import add_design_sections, stream_table_rows
from refluxion.units import UnitSet
from refluxion.utilities import Utility

K = 273.15


def test_aiche_unit_set():
    u = UnitSet("AICHE")
    assert (u.unit("temperature"), u.unit("pressure"), u.unit("mass_flow"), u.unit("mole_flow"), u.unit("duty"), u.unit("length")) == \
        ("C", "kPa", "kg/hr", "kmol/hr", "kJ/hr", "m")


def test_design_sections_render(tmp_path):
    fs = Flowsheet("t", components=[("BZ", "benzene"), ("TOL", "toluene")], method="IDEAL", units="AICHE")
    fs.feed("F", T=25, P=120, flows={"BZ": 500.0, "TOL": 500.0}, basis="mass")
    fs.add(RadFrac("COL", n_stages=20, condenser="total", reboiler="kettle", P_top=120, feeds={"F": 10},
                   specs={"reflux_ratio": 2.0, "distillate_mass_rate": 495.0}), inlets=["F"], outlets={"distillate": "D", "bottoms": "B"})
    fs.run()
    b = PlantBasis(cooling=[Utility("CW", "cooling", T_in=30 + K, T_out=40 + K, unit_price=3e-5, basis="kg", cp=4180.0)],
                   heating=[Utility("LPS", "heating", T_in=160 + K, T_out=160 + K, unit_price=0.02, basis="kg", latent=2080e3)],
                   electricity=Utility("E", "electricity", price=20.0), price={"BZ": 1.0, "TOL": 0.8})
    m = evaluate_plant(fs, b, products={"BZ": ("D", "BZ"), "TOL": ("B", "TOL")}, feeds=["F"], keys={"COL": ("BZ", "TOL")})
    rep = fs.report(title="t")
    add_design_sections(rep, m)
    html = rep.to_html()
    for title in ("Equipment summary", "Utilities summary", "Economics", "Cash flow"):
        assert title in html
    rows = stream_table_rows(fs, {"F": "Feed", "D": "Benzene product", "B": "Toluene product"})
    assert rows[0][:2] == ["1", "Feed"] and len(rows[0]) == 2 + 3 + 2 + 2   # number, description, T, P, total kg/h, wt fractions...


def test_report_renders_a_utility_exchanger_in_every_unit_set():
    from refluxion import UtilityHX
    cw = Utility("CW", "cooling", T_in=30 + K, T_out=40 + K, unit_price=3e-5, basis="kg", cp=4180.0)
    for us in ("AICHE", "SI-KW", "ENG", "METCBAR"):
        fs = Flowsheet("u", components=[("TOL", "toluene")], method="IDEAL", units="SI-KW")
        fs.feed("F", T=110, P=1.0, flows={"TOL": 100.0}, basis="mass")
        fs.add(UtilityHX("HX", utility=cw, T=50.0, P=0.0, U=850.0), inlets=["F"], outlets=["P"])
        fs.run()
        assert "HX" in fs.report(title="u", units=us).to_html()


def test_report_figure_embeds_an_svg(tmp_path):
    fs = Flowsheet("f", components=[("TOL", "toluene")], method="IDEAL", units="SI-KW")
    fs.feed("F", T=25, P=1.0, flows={"TOL": 1.0})
    rep = fs.report(title="f")
    rep.figure("Process flow diagram", '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>',
               caption="controls shown")
    assert "<circle" in rep.to_html() and "Process flow diagram" in rep.to_html()
    paths = rep.save(tmp_path / "f")
    assert "process-flow-diagram" in open(paths["markdown"], encoding="utf-8").read()
