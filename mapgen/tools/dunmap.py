"""Build a 3D dungeon map page from the client dats + world data.

Usage: python3 dunmap.py CONFIG.json [--info]
  --info  print the dungeon's contents (entrances, portals, doors, levers, chests, creatures) and exit.

Config keys (all optional except landblock/slug/title):
  landblock  "0119"
  slug       file name, e.g. "lightning-vault"
  title, subtitle
  group      index-page group label, e.g. "Elemental vaults"
  entrances  [{"name": "Entrance", "cell": "0x01190371", "pos": [60,-110,42]}] or [{"name": "Cave mouth", "outside": true}]
             default: every portal weenie whose destination is in this landblock
  routes     [{"name": "To the stone", "from": "Entrance", "to": [{"instance": "Major Sparking Stone"}]}]
             each "to" item: {"instance": NAME, "pick": "nearest"|"north"|"south"|"east"|"west"|"northeast"|... , "index": n}
                             or {"cell": "0x...", "pos": [x,y,z], "label": "..."}
             legs are walked in order; in-dungeon portals are used automatically.
  sweeps     [{"name": "Colossus sweep", "from": "Entrance", "creatures": ["Guruk Colossus"]}]
  targets    creature names to highlight (task targets)
  notes      [{"kind": "warn"|"tip"|"info", "title": "...", "body": "..."}]
  steps      ["quest step text", ...]   shown as an ordered list
  facts      [["label","value"], ...]   extra stat tiles
"""
import sys, os, re, json, math, heapq, collections, struct, base64
from html import escape
from itertools import count
from pathlib import Path
from array import array
import acdat

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
WD = ROOT / "world-data"
OUT = ROOT / "maps"
with (TOOLS.parent / "weenies.json").open(encoding="utf-8") as f:
    W = {int(k): v for k, v in json.load(f).items()}

_dats = {}
def dat(name):
    if name not in _dats:
        _dats[name] = acdat.Dat(acdat.DATDIR / f"client_{name}.dat")
    return _dats[name]


def load_cells(lb):
    cd, pd = dat("cell_1"), dat("portal")
    info = cd.get((lb << 16) | 0xFFFE)
    if info is None:
        raise ValueError(f"No landblock record for {lb:04X}")
    n = struct.unpack_from("<2I", info)[1]
    cells, envs = {}, {}
    for i in range(n):
        b = cd.get((lb << 16) | (0x100 + i))
        if b is None: continue
        c = acdat.env_cell(b); cells[c["id"]] = c
        if c["env"] not in envs:
            shape = pd.get(c["env"])
            envs[c["env"]] = acdat.environment(shape) if shape is not None else {}
    bad = [k for k, c in cells.items() if c["cs"] not in envs[c["env"]]]
    for k in bad: del cells[k]     # unreadable room shape: leave the room out rather than fail
    if bad: print("skipped unreadable cells:", [hex(k) for k in bad], file=sys.stderr)
    return cells, envs


def load_instances(lb):
    name = f"{lb:04X}.sql"
    for p in (WD / "ACE-World-16PY-Patches/Database/Patches/6 LandBlockExtendedData" / name,
              WD / "ACE-World-16PY/Database/3-Core/6 LandBlockExtendedData/SQL" / name):
        if p.is_file(): break
    else:
        return {}
    t = p.read_text(encoding="utf-8", errors="replace")
    inst = {}
    for m in re.finditer(r"VALUES \((0x[0-9A-Fa-f]+),\s*(\d+), (0x[0-9A-Fa-f]+), ([-\d.]+), ([-\d.]+), ([-\d.]+), [^)]*?(?:True|False), '[^']*'\); /\* (.*?) \*/", t):
        g, w, c, x, y, z, n = m.groups()
        inst[g] = dict(w=int(w), cell=int(c, 16), p=[float(x), float(y), float(z)], name=n)
    return inst


class Dungeon:
    def __init__(self, lb, climb=False):
        self.lb = lb; self.LB = lb << 16; self.climb = climb
        self.cells, self.E = load_cells(lb)
        self.inst = load_instances(lb)
        self._portals()

    def W(self, c, v):
        r = acdat.qrot(c["rot"], v); return (r[0] + c["pos"][0], r[1] + c["pos"][1], r[2] + c["pos"][2])

    def poly(self, c, pid):
        cs = self.E[c["env"]][c["cs"]]; P = [self.W(c, cs["verts"][v]) for v in cs["polys"][pid]]
        a, b, d = P[0], P[1], P[2]; u = [b[i] - a[i] for i in range(3)]; w = [d[i] - a[i] for i in range(3)]
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]); L = math.sqrt(sum(x * x for x in n)) or 1
        return P, (n[0] / L, n[1] / L, n[2] / L)

    def _portals(self):
        self.PI = {}
        self.outside = []
        # vertical extent and floor height of each cell, from its real geometry
        self.zlo, self.zhi, self.floorz, self.floors = {}, {}, {}, {}
        for cid, c in self.cells.items():
            cs = self.E[c["env"]][c["cs"]]
            zs = [self.W(c, v)[2] for v in cs["verts"].values()] or [c["pos"][2]]
            self.zlo[cid], self.zhi[cid] = min(zs), max(zs)
            fl = []
            pp = set(cs["portal_polys"])
            for pid in cs["polys"]:
                if pid in pp: continue
                P, n = self.poly(c, pid)
                if n[2] > 0.5: fl.append((P, n))
            self.floors[cid] = fl
            self.floorz[cid] = min(min(q[2] for q in P) for P, n in fl) if fl else self.zlo[cid]
        self._statics()
        for cid, c in self.cells.items():
            L = []
            for i, p in enumerate(c["portals"]):
                if p["poly"] not in self.E[c["env"]][c["cs"]]["polys"]:
                    o = self.LB | p["other"]; oc = self.cells.get(o)
                    if not oc or p["otherp"] >= len(oc["portals"]) or oc["portals"][p["otherp"]]["poly"] not in self.E[oc["env"]][oc["cs"]]["polys"]: continue
                    P, n = self.poly(oc, oc["portals"][p["otherp"]]["poly"]); n = (-n[0], -n[1], -n[2])
                else:
                    P, n = self.poly(c, p["poly"])
                cen = tuple(sum(q[k] for q in P) / len(P) for k in range(3))
                hz = abs(n[2]) > 0.6
                kind = ("down" if cen[2] - self.zlo[cid] < self.zhi[cid] - cen[2] else "up") if hz else "door"
                pt = cen if hz else (cen[0], cen[1], min(q[2] for q in P) + 0.3)
                if p["other"] == 0xFFFF:
                    self.outside.append((cid, pt)); continue
                o = self.LB | p["other"]
                if o not in self.cells: continue
                key = tuple(sorted([(cid, i), (o, p["otherp"])]))
                L.append(dict(other=o, kind=kind, pt=pt, P=P, key=key))
            self.PI[cid] = L
        # portal objects in this landblock
        self.tele = collections.defaultdict(list)    # cell -> [(pos, destcell, destpos, name)]
        self.exitcells = set()
        self.portal_objs = {}
        for guid, v in self.inst.items():
            wd = W.get(v["w"], {})
            if wd.get("cat") != "Portal" or v["cell"] & 0xFFFF < 0x100: continue
            dest = wd.get("dest")
            same = dest and (dest[0] >> 16) == self.lb and dest[0] in self.cells
            self.portal_objs[guid] = dict(name=v["name"], cell=v["cell"], p=v["p"], same=bool(same),
                                        dest=dest[0] if dest else 0)
            if same: self.tele[v["cell"]].append((tuple(v["p"]), dest[0], tuple(dest[1:]), v["name"]))
            else: self.exitcells.add(v["cell"])

    def ramp_point(self, cell, q, h):
        """where a sloped floor in `cell` meets the horizontal portal q (at height h), or None"""
        P = q["P"]; cx = sum(a[0] for a in P) / len(P); cy = sum(a[1] for a in P) / len(P)
        best = None
        for t in (0.15, 0.4):
            for a in P:
                sx, sy = a[0] + (cx - a[0]) * t, a[1] + (cy - a[1]) * t
                f = self.floor_at(cell, sx, sy, h)
                if f is not None and f > h - 1.0 and (best is None or f > best[2]): best = (sx, sy, f)
        f = self.floor_at(cell, cx, cy, h)
        if f is not None and f > h - 1.0 and (best is None or f > best[2]): best = (cx, cy, f)
        return None if best is None else (best[0], best[1], h + 0.3)

    def floor_at(self, cell, x, y, below):
        """height of the highest floor under (x, y) that is not above `below`"""
        best = None
        for P, n in self.floors.get(cell, []):
            if not self.inpoly(x, y, P): continue
            a = P[0]
            z = a[2] - (n[0] * (x - a[0]) + n[1] * (y - a[1])) / n[2]
            if z <= below + 0.6 and (best is None or z > best): best = z
        return best

    _shape_cache = {}

    def _shape(self, oid):
        """object-space physics polygons of a GfxObj (0x01) or Setup (0x02)"""
        if oid in Dungeon._shape_cache: return Dungeon._shape_cache[oid]
        pd = dat("portal"); out = []
        try:
            b = pd.get(oid)
            if b is None: pass
            elif oid >> 24 == 1:
                g = acdat.gfxobj_phys(b)
                if g: out = [[g[0][v] for v in vids] for vids in g[1].values()]
            elif oid >> 24 == 2:
                for gid, o, q, sc in acdat.setup_parts(b):
                    gb = pd.get(gid)
                    g = acdat.gfxobj_phys(gb) if gb else None
                    if not g: continue
                    for vids in g[1].values():
                        P = []
                        for v in vids:
                            x, y, z = g[0][v]; r = acdat.qrot(q, (x * sc[0], y * sc[1], z * sc[2]))
                            P.append((r[0] + o[0], r[1] + o[1], r[2] + o[2]))
                        out.append(P)
        except (struct.error, ValueError, KeyError):
            out = []
        Dungeon._shape_cache[oid] = out
        return out

    def _statics(self):
        """placed objects (stairs, bridges, gratings): their collision polygons become floors and walls"""
        self.static_polys = []
        bbox = {cid: (min(self.W(c, v)[0] for v in self.E[c["env"]][c["cs"]]["verts"].values()),
                      max(self.W(c, v)[0] for v in self.E[c["env"]][c["cs"]]["verts"].values()),
                      min(self.W(c, v)[1] for v in self.E[c["env"]][c["cs"]]["verts"].values()),
                      max(self.W(c, v)[1] for v in self.E[c["env"]][c["cs"]]["verts"].values()))
                for cid, c in self.cells.items() if self.E[c["env"]][c["cs"]]["verts"]}
        nbr = {cid: {self.LB | p["other"] for p in c["portals"] if p["other"] != 0xFFFF} for cid, c in self.cells.items()}
        for cid, c in self.cells.items():
            near = {cid} | nbr[cid] | {y for x in nbr[cid] for y in nbr.get(x, ())}
            for sid, o, q in c["statics"]:
                for P0 in self._shape(sid):
                    P = []
                    for v in P0:
                        r = acdat.qrot(q, v); P.append((r[0] + o[0], r[1] + o[1], r[2] + o[2]))
                    a, b, d = P[0], P[1], P[2]; u = [b[i] - a[i] for i in range(3)]; w = [d[i] - a[i] for i in range(3)]
                    n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]); L = math.sqrt(sum(x * x for x in n))
                    if L < 1e-6: continue
                    n = (n[0] / L, n[1] / L, n[2] / L)
                    self.static_polys.append((P, n))
                    if abs(n[2]) > 0.5:
                        if n[2] < 0: P = P[::-1]; n = (-n[0], -n[1], -n[2])   # double-sided top faces
                        cx = sum(q2[0] for q2 in P) / len(P); cy = sum(q2[1] for q2 in P) / len(P); cz = sum(q2[2] for q2 in P) / len(P)
                        for k in near:
                            bb = bbox.get(k)
                            if bb and bb[0] - 0.1 <= cx <= bb[1] + 0.1 and bb[2] - 0.1 <= cy <= bb[3] + 0.1 and self.zlo[k] - 0.6 <= cz <= self.zhi[k] + 0.6:
                                self.floors[k].append((P, n))

    @staticmethod
    def inpoly(x, y, P):
        ins = False; n = len(P)
        for i in range(n):
            x1, y1 = P[i][:2]; x2, y2 = P[(i + 1) % n][:2]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: ins = not ins
        return ins

    def fall(self, cell, x, y, depth=0, top=1e9):
        if depth > 60: return cell
        f = self.floor_at(cell, x, y, top)
        for q in self.PI[cell]:
            if q["kind"] == "down" and self.inpoly(x, y, q["P"]) and (f is None or f < q["pt"][2] - 0.3):
                return self.fall(q["other"], x, y, depth + 1, q["pt"][2])
        return cell

    def trap_drops(self):
        """down-holes whose fall ends in a cell holding an exit portal"""
        out = []
        seen = set()
        for cid, L in self.PI.items():
            for q in L:
                if q["kind"] != "down" or q["key"] in seen or not (self.floors.get(cid) or any(x["kind"] == "door" for x in L)): continue
                seen.add(q["key"])
                if self.ramp_point(q["other"], q, q["pt"][2]) is not None: continue   # ramp, not a hole
                land = self.fall(q["other"], q["pt"][0], q["pt"][1])
                if land in self.exitcells: out.append((q["pt"], land))
        # keep only the top of each shaft
        tops = []
        for pt, land in sorted(out, key=lambda a: -a[0][2]):
            if not any(abs(pt[0] - t[0]) < 3 and abs(pt[1] - t[1]) < 3 and l2 == land for t, l2 in tops): tops.append((pt, land))
        return tops

    def route(self, start, goal, pen_cells=None, pen=0):
        """start/goal = (cell, pos). Returns (length, [(pos, cell, how)])"""
        (sc, sp), (gc, gp) = start, goal
        sp, gp = tuple(sp), tuple(gp)
        dist = {(sc, "S"): 0}; prev = {}; pos = {(sc, "S"): sp}
        sequence = count()
        pq = [(0, sc, next(sequence), "S")]
        found = False
        while pq:
            d, c, _, k = heapq.heappop(pq)
            if k == "G": found = True; break
            if d > dist[(c, k)]: continue
            here = pos[(c, k)]; opts = []
            if c == gc: opts.append(((c, "G"), gp, math.dist(here, gp), "goal"))
            for q in self.PI.get(c, []):
                if q["key"] == k: continue
                w = math.dist(here, q["pt"])
                if q["kind"] == "door":
                    o = q["other"]
                    holes = [h for h in self.PI.get(o, []) if h["kind"] == "down"]
                    if not self.floors.get(o) and holes:      # no floor at all: walking in means falling
                        for q2 in self.PI.get(o, []):           # ...unless you jump straight across
                            if q2["kind"] != "door" or q2["key"] == q["key"]: continue
                            gap = math.dist(q["pt"][:2], q2["pt"][:2])
                            if gap <= 12 and abs(q2["pt"][2] - q["pt"][2]) < 1.0 and self.floors.get(q2["other"]):
                                opts.append(((q2["other"], q2["key"]), q2["pt"], w + gap + 25, "jump:%d" % round(gap)))
                        hx, hy, hz = holes[0]["pt"]
                        L = self.fall(holes[0]["other"], hx, hy, 0, hz)
                        if L in self.exitcells: continue
                        f2 = self.floor_at(L, hx, hy, hz)
                        opts.append(((L, ("drop", q["key"])), (hx, hy, (f2 if f2 is not None else self.floorz[L]) + 0.3), w + 3, "drop"))
                    else:
                        opts.append(((o, q["key"]), q["pt"], w, "walk"))
                elif q["kind"] == "down":
                    x, y, h = q["pt"]
                    rp = self.ramp_point(q["other"], q, h)
                    if rp is not None:                         # sloped floor continues: walk
                        opts.append(((q["other"], q["key"]), rp, math.dist(here, rp), "walk")); continue
                    L = self.fall(q["other"], x, y, 0, h)
                    if L in self.exitcells: continue
                    f2 = self.floor_at(L, x, y, h)
                    lp = (x, y, (f2 if f2 is not None else self.floorz[L]) + 0.3)
                    opts.append(((L, ("drop", q["key"])), lp, w + 3, "drop"))
                elif q["kind"] == "up":
                    rp = self.ramp_point(c, q, q["pt"][2])
                    if rp is not None:                         # ramp rises through the boundary
                        opts.append(((q["other"], q["key"]), rp, math.dist(here, rp), "walk"))
                    elif self.climb:   # stairs are separate objects here
                        f = self.floor_at(q["other"], q["pt"][0], q["pt"][1], q["pt"][2] + 0.5)
                        up = (q["pt"][0], q["pt"][1], (f if f is not None else q["pt"][2]) + 0.3)
                        opts.append(((q["other"], q["key"]), up, w + 6, "walk"))
            for tp, dc, dp, nm in self.tele.get(c, []):
                w = math.dist(here, tp)
                state = (dc, ("tp", nm, dp, tp))
                opts.append((state, dp, w + 5, "tp:" + nm))
            for s, p, w, how in opts:
                if pen_cells and s[0] in pen_cells: w += pen
                nd = d + w
                if nd < dist.get(s, 1e18):
                    dist[s] = nd; prev[s] = ((c, k), how); pos[s] = p; heapq.heappush(pq, (nd, s[0], next(sequence), s[1]))
        if not found: return None, None
        s = (gc, "G"); out = []
        while s in prev:
            parent, how = prev[s]
            out.append((pos[s], s[0], how))
            if how.startswith("tp:"):
                out.append((s[1][3], parent[0], "walk"))
            s = parent
        out.append((sp, sc, "start")); out.reverse()
        L = sum(math.dist(out[i][0], out[i - 1][0]) for i in range(1, len(out)) if not out[i][2].startswith("tp:"))
        return L, out

    # ---------------------------------------------------------------- spawns
    def spawns(self):
        """[(name, wcid, pos, cell)] for every creature that can be present"""
        out = []
        for v in self.inst.values():
            wd = W.get(v["w"], {})
            if wd.get("wtype") == 10 or wd.get("cat") == "Creature":
                out.append((v["name"], v["w"], v["p"], v["cell"]))
            elif wd.get("gen"):
                for prob, cw, delay, init, mx, where in wd["gen"]:
                    cd = W.get(cw, {})
                    if cw == 3666 or not cd: continue
                    if cd.get("wtype") == 10 or cd.get("cat") == "Creature":
                        for _ in range(max(1, mx)):
                            out.append((cd["name"], cw, v["p"], v["cell"]))
                    elif cd.get("gen"):     # nested generator
                        for prob2, cw2, _, _, mx2, _ in cd["gen"]:
                            c2 = W.get(cw2, {})
                            if c2.get("wtype") == 10 or c2.get("cat") == "Creature":
                                for _ in range(max(1, mx2)): out.append((c2["name"], cw2, v["p"], v["cell"]))
        return [s for s in out if s[3] & 0xFFFF >= 0x100]


def bearing(a, b):
    dx = b[0] - a[0]; dy = b[1] - a[1]
    if math.hypot(dx, dy) < 4: return ""
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((ang + 22.5) // 45) % 8]


def build(cfg, info=False):
    lb = int(cfg["landblock"], 16)
    if not info:
        slug = cfg["slug"]
        if not slug or slug in (".", "..") or any(c in slug for c in "/\\:\0"):
            raise ValueError("Expected a file name for the map slug")
    D = Dungeon(lb, climb=bool(cfg.get("climb_up")))
    cells = D.cells
    if not cells:
        raise ValueError(f"No readable cells in landblock {lb:04X}")
    def lookup(name, pick=None, index=None, near=None):
        cand = [(g, v) for g, v in D.inst.items() if v["name"].lower() == name.lower() and v["cell"] & 0xFFFF >= 0x100]
        if not cand: cand = [(g, v) for g, v in D.inst.items() if name.lower() in v["name"].lower() and v["cell"] & 0xFFFF >= 0x100]
        if not cand: raise SystemExit(f"no instance named {name!r}")
        if index is not None: cand = [cand[index]]
        dirs = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0), "northeast": (1, 1), "northwest": (-1, 1), "southeast": (1, -1), "southwest": (-1, -1), "down": None, "up": None}
        if pick in dirs and dirs[pick]:
            dx, dy = dirs[pick]; cand.sort(key=lambda gv: -(gv[1]["p"][0] * dx + gv[1]["p"][1] * dy))
        elif pick == "down": cand.sort(key=lambda gv: gv[1]["p"][2])
        elif pick == "up": cand.sort(key=lambda gv: -gv[1]["p"][2])
        elif near is not None: cand.sort(key=lambda gv: math.dist(gv[1]["p"], near))
        v = cand[0][1]
        return (v["cell"], tuple(v["p"]), v["name"])

    # entrances
    ents = {}
    auto = []
    for wd in W.values():
        d = wd.get("dest")
        if d and wd.get("cat") == "Portal" and (d[0] >> 16) == lb and d[0] in cells: auto.append((wd["name"], d[0], tuple(d[1:])))
    for e in cfg.get("entrances") or []:
        if e.get("outside"):
            if not D.outside: raise SystemExit("no outside openings")
            c, p = D.outside[e.get("index", 0)]; ents[e["name"]] = (c, (p[0], p[1], D.floorz[c] + 0.3))
        elif "cell" in e:
            ents[e["name"]] = (int(e["cell"], 16), tuple(e["pos"]))
        elif "portal" in e:
            m = [a for a in auto if a[0].lower() == e["portal"].lower()]
            if not m: raise SystemExit("no portal " + e["portal"])
            ents[e["name"]] = (m[0][1], m[0][2])
    if not ents:
        seen = set()
        for n, c, p in auto:
            if (c, p) in seen: continue
            seen.add((c, p)); ents[n if n not in ents else n + f" ({len(ents)})"] = (c, p)

    if info:
        print("cells", len(cells), "levels", sorted({round(c['pos'][2]) for c in cells.values()}, reverse=True))
        print("entrances (portals into here):"); [print("  ", n, hex(c), [round(x, 1) for x in p]) for n, c, p in sorted(set(auto))]
        print("outside openings:", [(hex(c), [round(x, 1) for x in p]) for c, p in D.outside[:6]], len(D.outside))
        print("portal objects:"); [print("  ", po["name"], hex(po["cell"]), [round(x, 1) for x in po["p"]], "-> same lb" if po["same"] else f"-> {po['dest']:08X}") for po in D.portal_objs.values()]
        cnt = collections.Counter(v["name"] for v in D.inst.values() if v["cell"] & 0xFFFF >= 0x100)
        print("objects:", dict(cnt.most_common()))
        for v in D.inst.values():
            wd = W.get(v["w"], {})
            if wd.get("cat") in ("Door", "Switch", "Chest", "PressurePlate", "HotSpot") or "trap" in v["name"].lower():
                print("  ", wd.get("cat"), v["name"], v["w"], hex(v["cell"]), [round(x, 1) for x in v["p"]], "LOCKED " + str(wd.get("lockcode")) if wd.get("locked") else "")
        sp = collections.Counter((n, W.get(w, {}).get("level")) for n, w, p, c in D.spawns())
        print("spawns:", dict(sp))
        print("trap drops:", [([round(x) for x in p], hex(l)) for p, l in D.trap_drops()])
        return

    # ---- geometry
    xs = [c["pos"][0] for c in cells.values()]; ys = [c["pos"][1] for c in cells.values()]; zs = [c["pos"][2] for c in cells.values()]
    CX = (min(xs) + max(xs)) / 2; CY = (min(ys) + max(ys)) / 2
    def Tp(p): return [round(p[0] - CX, 2), round(p[2], 2), round(-(p[1] - CY), 2)]
    floor = array("h"); wall = array("h")
    def triangles(P, n):
        if n[2] < -0.6: return
        tgt = floor if n[2] > 0.6 else wall
        Q = [(p[0] - CX, p[2], -(p[1] - CY)) for p in P]
        for i in range(1, len(Q) - 1):
            for v in (Q[0], Q[i], Q[i + 1]): tgt.extend(max(-32767, min(32767, int(round(x * 100)))) for x in v)
    for c in cells.values():
        cs = D.E[c["env"]][c["cs"]]
        pp = set(cs["portal_polys"])
        for pid in cs["polys"]:
            if pid not in pp:
                triangles(*D.poly(c, pid))
    for P, n in D.static_polys:
        triangles(P, n)
    # levels: distinct floor heights
    lv = []
    for z in sorted({round(D.floorz[c], 1) for c in cells}):
        if not lv or z - lv[-1] > 1.5: lv.append(z)
    def level_of(cell): z = D.floorz[cell]; return min(lv, key=lambda a: abs(a - z))

    # ---- routes
    def describe(out, endname):
        segs = []; acc = 0
        for i, (p, c, h) in enumerate(out):
            if i and not h.startswith("tp:"): acc += math.dist(p, out[i - 1][0])
            z = level_of(c)
            new = not segs or segs[-1]["z"] != z or h.startswith("tp:") or h.startswith("jump:")
            if new:
                if segs:
                    segs[-1]["exit"] = out[i - 1][0]; segs[-1]["end"] = acc
                    segs[-1]["how"] = "drop" if h == "drop" else h if h.startswith("tp:") or h.startswith("jump:") else ("down" if z < segs[-1]["z"] else "up")
                segs.append(dict(z=z, start=acc, arrive=p))
        segs[-1]["exit"] = out[-1][0]; segs[-1]["end"] = acc; segs[-1]["how"] = "end:" + endname
        res = []
        for s in segs:
            walk = round(s["end"] - s["start"]); d = bearing(s["arrive"], s["exit"]); d = (" " + d) if d else ""
            how = s["how"]
            if how == "drop": txt = f"Walk {walk} m{d}, then drop through the hole"
            elif how.startswith("jump:"): txt = f"Walk {walk} m{d}, then jump across the open gap ({how[5:]} m, run at full power)"
            elif how.startswith("tp:"): txt = f"Walk {walk} m{d} to the {how[3:]} portal and take it"
            elif how == "down": txt = f"Walk {walk} m{d} to the way down"
            elif how == "up": txt = f"Walk {walk} m{d} to the way up"
            else: txt = f"Walk {walk} m{d} to {how[4:]}"
            res.append(dict(z=round(s["z"], 1), start=round(s["start"]), text=txt, a=Tp(s["arrive"]), drop=how == "drop" or how.startswith("jump:")))
        return res

    def route_points(out):
        points = []
        for p, _, how in out:
            if how.startswith("tp:"):
                points.append(None)
            points.append(Tp(p))
        return points

    routes = []
    route_goals = {}
    palette = ["route", "alt", "r3", "r4"]
    for ri, r in enumerate(cfg.get("routes", [])):
        cur = ents[r.get("from") or next(iter(ents))]
        pts = []; steps = []; total = 0; drops = []; jumps = []; ok = True
        for li, leg in enumerate(r["to"]):
            if "instance" in leg:
                gc, gp, gname = lookup(leg["instance"], leg.get("pick"), leg.get("index"), near=cur[1])
                gname = leg.get("label", gname)
                route_goals[ri, li] = (gc, gp, gname)
            else:
                gc, gp, gname = int(leg["cell"], 16), tuple(leg["pos"]), leg.get("label", "the target")
            L, out = D.route(cur, (gc, gp))
            if out is None: print("NO ROUTE to", gname, "in", r["name"]); ok = False; break
            st = describe(out, gname)
            for s in st: s["start"] += round(total)
            steps += st
            drops += [Tp(out[i - 1][0]) + Tp(p) for i, (p, c, h) in enumerate(out) if h == "drop"]
            jumps += [Tp(out[i - 1][0]) + Tp(p) for i, (p, c, h) in enumerate(out) if h.startswith("jump:")]
            pts.append(route_points(out))
            total += L; cur = (gc, gp)
        if not ok: continue
        routes.append(dict(name=r["name"], color=palette[ri % 4], len=round(total), pts=pts, steps=steps, drops=drops, jumps=jumps,
                           levels=len({s["z"] for s in steps})))

    spawns = D.spawns()
    for sw in cfg.get("sweeps", []):
        want = {n.lower() for n in sw["creatures"]}
        tg = [(p, c) for n, w, p, c in spawns if n.lower() in want]
        if not tg: print("no spawns for sweep", sw["name"]); continue
        # cluster by proximity
        cl = []
        for p, c in tg:
            for k in cl:
                if math.dist(k["p"], p) < 14 and abs(k["p"][2] - p[2]) < 3: k["n"] += 1; break
            else: cl.append(dict(p=tuple(p), c=c, n=1))
        cur = ents[sw.get("from") or next(iter(ents))]
        left = cl[:]; pts = []; steps = []; total = 0; gi = 0
        while left:
            best = None
            for k in left:
                L, out = D.route(cur, (k["c"], k["p"]))
                if out is not None and (best is None or L < best[0]): best = (L, out, k)
            if best is None: print("unreachable groups:", len(left)); break
            L, out, k = best; left.remove(k); gi += 1
            st = describe(out, f"group {gi} ({k['n']} {sw['creatures'][0] if len(sw['creatures']) == 1 else 'targets'})")
            for s in st: s["start"] += round(total)
            steps += st
            pts.append(route_points(out)); total += L; cur = (k["c"], k["p"])
        routes.append(dict(name=sw["name"], color=palette[(len(routes)) % 4], len=round(total), pts=pts, steps=steps, drops=[],
                           levels=len({s["z"] for s in steps}), sweep=True, groups=gi))

    # ---- markers & monsters
    targets = {t.lower() for t in cfg.get("targets", [])}
    mons = {}
    sp_out = []
    for n, w, p, c in spawns:
        wd = W.get(w, {})
        if n not in mons: mons[n] = dict(name=n, level=wd.get("level"), n=0, res=wd.get("res", {}), target=n.lower() in targets)
        mons[n]["n"] += 1
        sp_out.append(Tp(p) + [1 if n.lower() in targets else 0])
    objs = []
    for guid, v in D.inst.items():
        if v["cell"] & 0xFFFF < 0x100: continue
        wd = W.get(v["w"], {})
        cat = wd.get("cat")
        kind = None
        if cat == "Portal":
            kind = "tele" if D.portal_objs[guid]["same"] else "exit"
        elif cat == "Door": kind = "locked" if wd.get("locked") else "door"
        elif cat == "Switch": kind = "lever"
        elif cat == "Chest": kind = "chest"
        elif cat in ("PressurePlate", "HotSpot") or "trap" in v["name"].lower(): kind = "trap"
        if kind: objs.append(dict(k=kind, n=v["name"], p=Tp(v["p"]), z=round(v["p"][2], 1), c=v["cell"]))
    named = []
    for t in cfg.get("labels", []):
        c, p, n = lookup(t["instance"], t.get("pick"), t.get("index"))
        named.append(dict(n=t.get("label", n), p=Tp(p), k=t.get("kind", "goal"), c=c))
    for ri, route in enumerate(cfg.get("routes", [])):
        for li, leg in enumerate(route["to"]):
            if "instance" not in leg or leg.get("nolabel"):
                continue
            goal = route_goals.get((ri, li))
            if goal is None:
                # Later goals still get markers when an earlier leg has no route.
                goal = lookup(leg["instance"], leg.get("pick"), leg.get("index"))
            cell, pos, name = goal
            name = leg.get("label", name)
            point = Tp(pos)
            if not any(n["n"] == name and n["p"] == point for n in named):
                named.append(dict(n=name, p=point, k="goal", c=cell))
    entr = [dict(n=n, p=Tp(p), c=c) for n, (c, p) in ents.items()]
    traps = [dict(p=Tp(p), z=round(p[2]), land=round(D.floorz[l])) for p, l in D.trap_drops()]

    data = dict(meta=dict(title=cfg["title"], subtitle=cfg.get("subtitle", ""), lb=f"{lb:04X}", notes=cfg.get("notes", []),
                          steps=cfg.get("steps", []), facts=cfg.get("facts", []), group=cfg.get("group", "")),
                levels=[round(z, 1) for z in sorted(lv, reverse=True)], zmin=min(zs), zmax=max(zs) + 6,
                ext=[round(max(xs) - min(xs) + 20), round(max(ys) - min(ys) + 20)],
                floor=base64.b64encode(floor.tobytes()).decode(), wall=base64.b64encode(wall.tobytes()).decode(),
                routes=routes, mons=list(mons.values()), spawns=sp_out, objs=objs, named=named, ents=entr, traps=traps)
    if os.environ.get("DUNMAP_GAME"):   # map data for the OpenAC plugin instead of the web page
        import gameexport
        res = gameexport.export(cfg, data, CX, CY, floor, os.environ["DUNMAP_GAME"], wall)
        print(json.dumps(res))
        return res
    summaries = OUT / "_summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    tpl = (TOOLS / "dunmap_template.html").read_text(encoding="utf-8")
    tpl = tpl.replace("/*__THEME__*/", (TOOLS / "theme.css").read_text(encoding="utf-8") + (TOOLS / "map.css").read_text(encoding="utf-8"))
    js = json.dumps(data, separators=(",", ":")).replace("<", r"\u003c")
    full = tpl.replace("__DATA__", js).replace("__TITLE__", escape(cfg["title"]) + " Map", 1)
    (OUT / (cfg["slug"] + ".html")).write_text(full, encoding="utf-8")
    summary = dict(slug=cfg["slug"], title=cfg["title"], subtitle=cfg.get("subtitle", ""), group=cfg.get("group", ""),
                   routes=[(r["name"], r["len"], r["levels"]) for r in routes], cells=len(cells), tris=(len(floor) + len(wall)) // 9,
                   targets=[(m["name"], m["level"], m["n"]) for m in mons.values() if m["target"]],
                   kb=len(full) // 1024, traps=len(traps), locked=sum(1 for o in objs if o["k"] == "locked"))
    with (summaries / (cfg["slug"] + ".json")).open("w", encoding="utf-8") as f:
        json.dump(summary, f)
    print(json.dumps(summary))


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    build(cfg, info="--info" in sys.argv)
