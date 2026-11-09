"""The flowsheet: blocks connected by streams, solved sequential-modular style.

How it runs (the same strategy as Aspen's default engine):

1. **Sequencing** - blocks are nodes, streams are edges. Strongly connected components (Tarjan's
   algorithm) reveal recycle loops. Each loop gets *tear streams* - the smallest set of streams
   that, once guessed, makes the loop a straight sequence.
2. **Convergence** - each loop is iterated: guess the tear streams, run the blocks, compare what
   comes back with the guess, and update with Wegstein acceleration (bounded between -5 and 0,
   Aspen's defaults) until every tear variable agrees within the tolerance.
3. **Design specs** - wrapped around the whole flowsheet: vary an input until a computed result
   hits its target (secant method for one spec, Broyden/hybrid for several).

The run log mirrors Aspen's Control Panel, and the convergence history is kept for reports.
"""

from __future__ import annotations

import copy
import itertools
import time
from dataclasses import dataclass, field

import numpy as np

from .blocks.base import Block, BlockError
from .components import ComponentList
from .props import make_method
from .stream import Stream
from .units import as_unitset


class ConvergenceError(RuntimeError):
    pass


@dataclass
class Loop:
    name: str
    blocks: list
    tears: list
    method: str = "WEGSTEIN"
    maxit: int = 30
    tol: float = 1e-4
    history: list = field(default_factory=list)   # max(err/tol) per iteration
    converged: bool = False
    iterations: int = 0


@dataclass
class DesignSpec:
    name: str
    measure: object        # callable(fs) -> value in the flowsheet's units
    target: float
    tol: float
    vary: str              # path, e.g. "LFLOW.fractions.CHRCY"
    lower: float
    upper: float
    label: str = ""
    result: dict = field(default_factory=dict)


@dataclass
class Calculator:
    name: str
    fn: object
    before: str | None = None
    after: str | None = None


class Flowsheet:
    """A process flowsheet.

    >>> fs = Flowsheet("Cumene", components=[("C6H6", "benzene"), ...], method="RK-SOAVE", units="ENG")
    """

    def __init__(self, name: str, components, method: str = "PENG-ROB", units="ENG",
                 kij: dict | None = None, nrtl: dict | None = None, description: str = ""):
        self.name = name
        self.description = description
        self.comps = components if isinstance(components, ComponentList) else ComponentList(components)
        self.units = as_unitset(units)
        self.method_name = method
        self.pm = make_method(method, self.comps, kij=kij, nrtl=nrtl)
        self.blocks: dict[str, Block] = {}
        self.streams: dict[str, Stream] = {}
        self.design_specs: list[DesignSpec] = []
        self.calculators: list[Calculator] = []
        self.user_tears: list[str] = []
        self.loop_options = dict(method="WEGSTEIN", maxit=30, tol=1e-4, qmin=-5.0, qmax=0.0)
        self.log: list[str] = []
        self.loops: list[Loop] = []
        self.sequence: list = []
        self.status = "not run"
        self.run_time = 0.0

    # --- building -----------------------------------------------------------------------------
    def _stream(self, name: str) -> Stream:
        if name not in self.streams:
            s = Stream(name)
            s.fs = self
            self.streams[name] = s
        return self.streams[name]

    def feed(self, name: str, *, T=None, P=None, vf=None, flows: dict | None = None, total=None,
             fractions: dict | None = None, basis: str = "mole") -> Stream:
        """Define a feed stream. Two of T, P, vf; either ``flows={comp: flow}`` or
        ``total=`` with ``fractions={comp: frac}``; ``basis`` = "mole" or "mass"."""
        s = self._stream(name)
        s.spec = dict(T=T, P=P, vf=vf, flows=dict(flows or {}), total=total,
                      fractions=dict(fractions or {}), basis=basis)
        return s

    def add(self, block: Block, inlets=None, outlets=None) -> Block:
        """Add a block and connect streams to its ports.

        ``inlets``/``outlets`` may be a list of stream names (assigned to the block's ports in
        order; extra inlets go to the first port) or a dict ``{port: name or [names]}``. Blocks
        without fixed ports (FSplit, Sep, Dupl) take their outlet stream names as ports.
        """
        if block.name in self.blocks:
            raise ValueError(f"duplicate block name {block.name!r}")
        block.fs = self
        self.blocks[block.name] = block
        for port, names in self._port_map(block, inlets, "in").items():
            for n in names:
                s = self._stream(n)
                if s.dest is not None:
                    raise ValueError(f"stream {n} already feeds {s.dest[0].name}")
                s.dest = (block, port)
                block.inlets.setdefault(port, []).append(s)
        for port, names in self._port_map(block, outlets, "out").items():
            for n in names:
                s = self._stream(n)
                if s.source is not None:
                    raise ValueError(f"stream {n} is already produced by {s.source[0].name}")
                s.source = (block, port)
                block.outlets[port] = s
        return block

    def _port_map(self, block, spec, direction):
        ports = block.inlet_ports if direction == "in" else block.outlet_ports
        if spec is None:
            return {}
        if isinstance(spec, str):
            spec = [spec]
        if isinstance(spec, dict):
            return {p: ([v] if isinstance(v, str) else list(v)) for p, v in spec.items()}
        spec = list(spec)
        if not ports:  # free-form ports named after the streams
            return {n: [n] for n in spec}
        if direction == "in":
            if len(ports) == 1:
                return {ports[0]: spec}
            return {p: [n] for p, n in zip(ports, spec)}
        if len(spec) > len(ports):
            raise ValueError(f"{block.name}: {len(spec)} outlets given but ports are {ports}")
        return {p: [n] for p, n in zip(ports, spec)}

    def tear(self, *names: str):
        """Choose tear streams yourself (otherwise they are picked automatically)."""
        self.user_tears.extend(names)
        return self

    def estimate(self, name: str, *, T, P, flows: dict):
        """Initial guess for a tear stream (default is zero flow)."""
        self._stream(name).estimate = dict(T=T, P=P, flows=flows)
        return self

    def design_spec(self, name, *, measure, target, vary, lower, upper, tol=None, label=""):
        """Aspen-style design spec: vary ``vary`` (a path such as "COOL.T") between ``lower`` and
        ``upper`` until ``measure(fs)`` equals ``target`` (all in the flowsheet's units)."""
        tol = tol if tol is not None else max(abs(target) * 1e-4, 1e-8)
        self.design_specs.append(DesignSpec(name, measure, float(target), float(tol), vary, float(lower),
                                            float(upper), label or name))
        return self

    def calculator(self, name, fn, before: str | None = None, after: str | None = None):
        """Run ``fn(fs)`` each pass - before/after a named block, or at the start of the pass."""
        self.calculators.append(Calculator(name, fn, before, after))
        return self

    # --- access -----------------------------------------------------------------------------
    def __getitem__(self, name):
        if name in self.blocks:
            return self.blocks[name]
        if name in self.streams:
            return self.streams[name]
        raise KeyError(f"no block or stream named {name!r}")

    def stream(self, name) -> Stream:
        return self.streams[name]

    def block(self, name) -> Block:
        return self.blocks[name]

    @property
    def feeds(self):
        return [s for s in self.streams.values() if s.source is None]

    @property
    def products(self):
        return [s for s in self.streams.values() if s.dest is None and s.source is not None]

    def get_path(self, path: str):
        obj, keys = self._resolve(path)
        for k in keys[:-1]:
            obj = obj[self._key(obj, k)]
        return obj.get(self._key(obj, keys[-1])) if isinstance(obj, dict) else obj[keys[-1]]

    def set_path(self, path: str, value):
        obj, keys = self._resolve(path)
        for k in keys[:-1]:
            kk = self._key(obj, k)
            if kk not in obj or obj[kk] is None:
                obj[kk] = {}
            obj = obj[kk]
        obj[self._key(obj, keys[-1])] = value

    def _resolve(self, path):
        head, *keys = path.split(".")
        if head in self.blocks:
            return self.blocks[head].spec, keys
        if head in self.streams and self.streams[head].spec is not None:
            return self.streams[head].spec, keys
        raise KeyError(f"{path!r}: {head} is not a block or a feed stream")

    @staticmethod
    def _key(d, k):
        if isinstance(d, dict) and k not in d:
            try:
                ik = int(k)
                if ik in d or all(isinstance(x, int) for x in d):
                    return ik
            except ValueError:
                pass
        return k

    def snapshot(self):
        return {"blocks": {n: copy.deepcopy(b.spec) for n, b in self.blocks.items()},
                "streams": {n: copy.deepcopy(s.spec) for n, s in self.streams.items() if s.spec is not None}}

    def restore(self, snap):
        for n, spec in snap["blocks"].items():
            self.blocks[n].spec = copy.deepcopy(spec)
        for n, spec in snap["streams"].items():
            self.streams[n].spec = copy.deepcopy(spec)

    # --- sequencing -------------------------------------------------------------------------
    def _edges(self):
        edges = []  # (source block, dest block, stream)
        for s in self.streams.values():
            if s.source is not None and s.dest is not None:
                edges.append((s.source[0].name, s.dest[0].name, s.name))
        return edges

    def _sccs(self):
        names = list(self.blocks)
        adj = {n: [] for n in names}
        for a, b, _ in self._edges():
            adj[a].append(b)
        index, low, on, stack, out = {}, {}, set(), [], []
        counter = itertools.count()

        def strong(v):
            index[v] = low[v] = next(counter)
            stack.append(v)
            on.add(v)
            for w in adj[v]:
                if w not in index:
                    strong(w)
                    low[v] = min(low[v], low[w])
                elif w in on:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)

        for n in names:
            if n not in index:
                strong(n)
        return out[::-1]  # Tarjan emits reverse topological order

    @staticmethod
    def _toposort(nodes, edges, order_hint):
        indeg = {n: 0 for n in nodes}
        adj = {n: [] for n in nodes}
        for a, b in edges:
            adj[a].append(b)
            indeg[b] += 1
        ready = sorted([n for n in nodes if indeg[n] == 0], key=order_hint.index)
        out = []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
                    ready.sort(key=order_hint.index)
        return out if len(out) == len(nodes) else None

    def _build_sequence(self):
        order_hint = list(self.blocks)
        edges = self._edges()
        seq, loops = [], []
        loop_no = 0
        for comp in self._sccs():
            comp_set = set(comp)
            internal = [(a, b, s) for a, b, s in edges if a in comp_set and b in comp_set]
            if len(comp) == 1 and not internal:
                seq.append(self.blocks[comp[0]])
                continue
            # choose tears
            user = [e for e in internal if e[2] in self.user_tears]
            chosen = None
            if user and self._toposort(comp, [(a, b) for a, b, s in internal if (a, b, s) not in user], order_hint):
                chosen = user
            else:
                def backwardness(combo):
                    return -sum(order_hint.index(a) - order_hint.index(b) for a, b, _ in combo)
                for k in range(1, len(internal) + 1):
                    cands = [c for c in itertools.combinations(internal, k)
                             if self._toposort(comp, [(a, b) for a, b, s in internal if (a, b, s) not in c], order_hint)]
                    if cands:
                        chosen = list(min(cands, key=backwardness))
                        break
            rest = [(a, b) for a, b, s in internal if (a, b, s) not in chosen]
            order = self._toposort(comp, rest, order_hint)
            loop_no += 1
            loop = Loop(f"$OLVER{loop_no:02d}", [self.blocks[n] for n in order], [self.streams[s] for _, _, s in chosen],
                        method=self.loop_options["method"], maxit=self.loop_options["maxit"], tol=self.loop_options["tol"])
            seq.append(loop)
            loops.append(loop)
        self.sequence, self.loops = seq, loops
        return seq

    def describe_sequence(self) -> str:
        parts = []
        for item in (self.sequence or self._build_sequence()):
            if isinstance(item, Loop):
                parts.append(f"{item.name} [tear {', '.join(s.name for s in item.tears)}: "
                             f"{' -> '.join(b.name for b in item.blocks)}]")
            else:
                parts.append(item.name)
        return " -> ".join(parts)

    # --- running ------------------------------------------------------------------------------
    def _msg(self, text):
        self.log.append(text)

    def _set_feed(self, s: Stream):
        u = self.units
        sp = s.spec
        n = len(self.comps)
        flows = np.zeros(n)
        qty = "mole_flow" if sp.get("basis", "mole") == "mole" else "mass_flow"
        if sp["flows"]:
            for cid, v in sp["flows"].items():
                flows[self.comps.idx(cid)] = u.to_si(v, qty)
        elif sp.get("total") is not None:
            frac = np.zeros(n)
            for cid, v in sp["fractions"].items():
                frac[self.comps.idx(cid)] = float(v)
            frac = frac / frac.sum()
            flows = frac * u.to_si(sp["total"], qty)
        if qty == "mass_flow":
            flows = flows / self.comps.MW * 1000.0
        T = u.to_si(sp["T"], "temperature") if sp.get("T") is not None else None
        P = u.to_si(sp["P"], "pressure") if sp.get("P") is not None else None
        s.set_state(flows, T=T, P=P, vf=sp.get("vf"))

    def _init_tear(self, s: Stream):
        if s.solved:
            return
        if s.estimate:
            e = s.estimate
            flows = np.zeros(len(self.comps))
            for cid, v in e["flows"].items():
                flows[self.comps.idx(cid)] = self.units.to_si(v, "mole_flow")
            s.set_state(flows, T=self.units.to_si(e["T"], "temperature"), P=self.units.to_si(e["P"], "pressure"))
        else:
            s.set_state(np.zeros(len(self.comps)), T=298.15, P=101325.0)

    def _run_block(self, b: Block):
        for c in self.calculators:
            if c.before == b.name:
                c.fn(self)
        try:
            b.run()
        except BlockError:
            b.status = "error"
            raise
        except Exception as exc:
            b.status = "error"
            raise BlockError(f"{b.name} ({b.type_name}) failed: {exc}") from exc
        for c in self.calculators:
            if c.after == b.name:
                c.fn(self)

    @staticmethod
    def _tear_vector(s: Stream):
        return np.concatenate([s.flows, [s.h, s.P]])

    def _set_tear(self, s: Stream, x):
        C = len(self.comps)
        flows = np.maximum(x[:C], 0.0)
        s.set_state(flows, P=x[C + 1], H=x[C], T0=s.state.T if s.state else None) if flows.sum() > 0 \
            else s.set_state(flows, T=s.state.T if s.state else 298.15, P=x[C + 1])

    def _converge_loop(self, loop: Loop):
        C = len(self.comps)
        for s in loop.tears:
            self._init_tear(s)
        opts = self.loop_options
        x = np.concatenate([self._tear_vector(s) for s in loop.tears])
        n = len(x) // (C + 2)
        x_prev = g_prev = None
        loop.history = []
        self._msg(f"-> Loop {loop.name}  method: {loop.method}  tear: {', '.join(s.name for s in loop.tears)}")
        for it in range(1, loop.maxit + 1):
            for b in loop.blocks:
                self._run_block(b)
            g = np.concatenate([self._tear_vector(s) for s in loop.tears])
            # scaled error: flows relative to the stream total, h relative to max(|h|, 1 kJ/mol)
            err = []
            for k in range(n):
                seg = slice(k * (C + 2), (k + 1) * (C + 2))
                gx, xx = g[seg], x[seg]
                Ft = max(gx[:C].sum(), xx[:C].sum(), 1e-12)
                e_flow = np.abs(gx[:C] - xx[:C]) / np.maximum(np.abs(gx[:C]), 1e-6 * Ft + 1e-12)
                e_flow = np.where(np.abs(gx[:C] - xx[:C]) < 1e-9 * Ft, 0.0, e_flow)
                e_h = abs(gx[C] - xx[C]) / max(abs(gx[C]), 1000.0)
                e_p = abs(gx[C + 1] - xx[C + 1]) / max(abs(gx[C + 1]), 1.0)
                err.append(max(e_flow.max(initial=0.0), e_h, e_p))
            ratio = max(err) / loop.tol
            loop.history.append(ratio)
            self._msg(f"   Loop {loop.name}  iteration {it:3d}   max err/tol {ratio: .5E}")
            if ratio < 1.0:
                loop.converged, loop.iterations = True, it
                self._msg(f"   Loop {loop.name} converged in {it} iterations")
                return
            # Wegstein update
            if x_prev is None or loop.method.upper() == "DIRECT":
                x_new = g.copy()
            else:
                dx = x - x_prev
                dg = g - g_prev
                with np.errstate(divide="ignore", invalid="ignore"):
                    s_ = np.where(np.abs(dx) > 1e-14 * np.maximum(np.abs(x), 1.0), dg / dx, 0.0)
                    q = np.where(np.abs(s_ - 1.0) > 1e-12, s_ / (s_ - 1.0), 0.0)
                q = np.clip(np.nan_to_num(q), opts["qmin"], opts["qmax"])
                x_new = q * x + (1.0 - q) * g
            x_prev, g_prev = x, g
            x = x_new
            for k, s in enumerate(loop.tears):
                self._set_tear(s, x[k * (C + 2):(k + 1) * (C + 2)])
        loop.iterations = loop.maxit
        self._msg(f"*** Loop {loop.name} did NOT converge in {loop.maxit} iterations "
                  f"(max err/tol {loop.history[-1]:.3g}). Try more iterations or a tear estimate.")
        raise ConvergenceError(f"{loop.name} not converged after {loop.maxit} iterations")

    def _run_pass(self):
        for s in self.feeds:
            if s.spec is None:
                raise ValueError(f"stream {s.name} has no source block and no feed specification")
            self._set_feed(s)
        for c in self.calculators:
            if c.before is None and c.after is None:
                c.fn(self)
        for item in self.sequence:
            if isinstance(item, Loop):
                self._converge_loop(item)
            else:
                self._run_block(item)

    def run(self, verbose: bool = False):
        """Solve the flowsheet (including design specs). Returns self."""
        t0 = time.perf_counter()
        self.log = [f"Flowsheet {self.name}: {len(self.blocks)} blocks, {len(self.streams)} streams, "
                    f"property method {self.pm.name}"]
        self._build_sequence()
        self._msg("Calculation sequence: " + self.describe_sequence())
        try:
            if self.design_specs:
                self._solve_design_specs()
            else:
                self._run_pass()
            self.status = "converged"
            self._msg("Simulation converged." if not self.warnings() else "Simulation completed with warnings.")
        except Exception as exc:
            self.status = "error"
            self._msg(f"*** ERROR: {exc}")
            if verbose:
                print("\n".join(self.log))
            raise
        finally:
            self.run_time = time.perf_counter() - t0
        if verbose:
            print("\n".join(self.log))
        return self

    def _solve_design_specs(self):
        from scipy.optimize import brentq, root

        specs = self.design_specs

        def evaluate(values):
            for ds, v in zip(specs, values):
                self.set_path(ds.vary, float(v))
            self._run_pass()
            return np.array([(ds.measure(self) - ds.target) / max(abs(ds.tol), 1e-300) for ds in specs])

        x0 = []
        for ds in specs:
            cur = self.get_path(ds.vary)
            x0.append(float(cur) if cur is not None else 0.5 * (ds.lower + ds.upper))
        self._msg(f"-> Design specs: {', '.join(ds.name for ds in specs)}")
        if len(specs) == 1:
            ds = specs[0]
            lo, hi = ds.lower, ds.upper
            x = min(max(x0[0], lo), hi)
            f0 = evaluate([x])[0]
            self._msg(f"   {ds.name}: vary {ds.vary} = {x:.6g}  error/tol = {f0: .4E}")
            if abs(f0) <= 1.0:
                ds.result = dict(converged=True, value=x, measured=ds.measure(self))
                return
            # secant steps, falling back to a bracketed root when a sign change appears
            xs, fs_ = [x], [f0]
            step = 0.05 * (hi - lo) if hi > lo else 1.0
            x1 = min(x + step, hi)
            if x1 == x:
                x1 = max(x - step, lo)
            for it in range(40):
                f1 = evaluate([x1])[0]
                xs.append(x1)
                fs_.append(f1)
                self._msg(f"   {ds.name}: vary {ds.vary} = {x1:.6g}  error/tol = {f1: .4E}")
                if abs(f1) <= 1.0:
                    ds.result = dict(converged=True, value=x1, measured=ds.measure(self), iterations=it + 2)
                    return
                sign_change = [(xa, xb) for (xa, fa), (xb, fb) in zip(zip(xs, fs_), zip(xs[1:], fs_[1:])) if fa * fb < 0]
                if sign_change:
                    a, b = sign_change[-1]
                    a, b = min(a, b), max(a, b)
                    xr = brentq(lambda v: evaluate([v])[0], a, b, xtol=1e-12 * max(1, abs(a)), maxiter=100)
                    evaluate([xr])
                    ds.result = dict(converged=True, value=xr, measured=ds.measure(self), iterations=it + 2)
                    self._msg(f"   {ds.name} converged: {ds.vary} = {xr:.6g}")
                    return
                x_prev, f_prev = xs[-2], fs_[-2]
                if f1 != f_prev:
                    x2 = x1 - f1 * (x1 - x_prev) / (f1 - f_prev)
                else:
                    x2 = x1 + step
                x2 = min(max(x2, lo), hi)
                if x2 == x1:  # stuck at a bound
                    break
                x1 = x2
            ds.result = dict(converged=False, value=x1, measured=ds.measure(self))
            self._msg(f"*** {ds.name}: unable to meet the spec within {ds.vary} bounds [{lo}, {hi}] "
                      f"(stopped at {x1:.6g}, measured {ds.measure(self):.6g}, target {ds.target:.6g})")
            raise ConvergenceError(f"design spec {ds.name} not satisfied within the vary bounds")
        sol = root(lambda v: evaluate(np.clip(v, [d.lower for d in specs], [d.upper for d in specs])), x0,
                   method="hybr", options={"xtol": 1e-10})
        vals = np.clip(sol.x, [d.lower for d in specs], [d.upper for d in specs])
        res = evaluate(vals)
        ok = bool(np.all(np.abs(res) <= 1.0))
        for ds, v in zip(specs, vals):
            ds.result = dict(converged=ok, value=float(v), measured=ds.measure(self))
        if not ok:
            raise ConvergenceError("design specs not satisfied: " + ", ".join(ds.name for ds in specs))

    # --- checks & summaries -----------------------------------------------------------------
    def warnings(self) -> list[str]:
        out = []
        for b in self.blocks.values():
            for m in b.messages:
                out.append(f"{b.name}: {m}")
        return out

    def balance_check(self) -> list[dict]:
        rows = []
        for b in self.blocks.values():
            if b.status in ("not run", "error"):
                continue
            rows.append(dict(block=b.name, type=b.type_name, mass=b.mass_balance_error(), energy=b.energy_balance_error()))
        return rows

    def overall_balance(self) -> dict:
        mi = sum(s.mass_flow for s in self.feeds if s.solved)
        mo = sum(s.mass_flow for s in self.products if s.solved)
        return dict(mass_in=mi, mass_out=mo, rel_error=(mi - mo) / max(mi, 1e-30))

    def report(self, path=None, **kwargs):
        """Build the HTML + Markdown report (see :mod:`refluxion.report`)."""
        from .report import Report

        rep = Report(self, **kwargs)
        if path:
            rep.save(path)
        return rep

    def __repr__(self) -> str:
        return f"<Flowsheet {self.name}: {len(self.blocks)} blocks, {self.pm.name}, {self.status}>"
