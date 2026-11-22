"""Writes one dungeon's map for the OpenAC plugin: JSON, a mesh and one floor image per level.

Called from dunmap.build() when DUNMAP_GAME names an output folder. Everything is written in the
landblock's own frame (metres, x east and y north from the south-west corner, z up), which is the
frame OpenAC's PluginDungeonFloorplan.ToLandblockLocal gives for the player.
"""
import os, json, math, struct
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BAND = 6.0          # metres per level band, shifted down 3 m, the same banding OpenAC's floorplan uses
MAX_PX = 2000       # plugin images may be at most 2048 px a side
PX_PER_M = 4.0
FLOOR = (78, 59, 38, 240)
GHOST = (201, 162, 90, 34)
WALL = (214, 178, 104, 255)


def band_of(z):
    return math.floor((z + BAND / 2) / BAND)


def export(cfg, data, CX, CY, floor, out_dir, wall=None):
    lb = data["meta"]["lb"]
    def raw(t):   # undo dunmap's three.js transform: [x - CX, z, -(y - CY)]
        return [round(t[0] + CX, 2), round(CY - t[2], 2), round(t[1], 2)]

    tris = []
    for i in range(0, len(floor), 9):
        v = [floor[i + k] / 100 for k in range(9)]
        tris.append([raw(v[0:3]), raw(v[3:6]), raw(v[6:9])])
    xs = [p[0] for t in tris for p in t]; ys = [p[1] for t in tris for p in t]
    x0, x1, y0, y1 = min(xs) - 6, max(xs) + 6, min(ys) - 6, max(ys) + 6
    s = min(PX_PER_M, MAX_PX / max(x1 - x0, y1 - y0))
    w, h = int(math.ceil((x1 - x0) * s)), int(math.ceil((y1 - y0) * s))
    def px(p): return ((p[0] - x0) * s, (y1 - p[1]) * s)

    by_band = {}
    for t in tris:
        by_band.setdefault(band_of(sum(p[2] for p in t) / 3), []).append(t)
    masks = {}
    for b, ts in by_band.items():
        m = Image.new("L", (w, h), 0); d = ImageDraw.Draw(m)
        for t in ts: d.polygon([px(p) for p in t], fill=255)
        masks[b] = m
    # drop slivers: bands with under 12 square metres of floor are ramps between levels
    keep = [b for b in sorted(masks, reverse=True) if np.count_nonzero(np.asarray(masks[b])) / (s * s) >= 12] or sorted(masks, reverse=True)[:1]
    allm = Image.fromarray(np.maximum.reduce([np.asarray(m) for m in masks.values()]))
    os.makedirs(out_dir, exist_ok=True)
    bands = []
    grow = max(3, int(round(s * 0.6))) | 1
    for i, b in enumerate(keep):
        # merge slivers into the nearest kept band so ramps still show
        m = Image.fromarray(np.maximum.reduce([np.asarray(masks[o]) for o in masks if o == b or (o not in keep and min(keep, key=lambda k: abs(k - o)) == b)]))
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        img.paste(Image.new("RGBA", (w, h), GHOST), (0, 0), allm)
        edge = m.filter(ImageFilter.MaxFilter(grow))
        img.paste(Image.new("RGBA", (w, h), WALL), (0, 0), edge)
        img.paste(Image.new("RGBA", (w, h), FLOOR), (0, 0), m.filter(ImageFilter.MinFilter(3)) if s >= 3 else m)
        name = f"{lb}_{i + 1}.png"
        img.save(os.path.join(out_dir, name), optimize=True)
        bands.append(dict(band=b, zlo=b * BAND - BAND / 2, zhi=b * BAND + BAND / 2, img=name))

    routes = []
    for r in data["routes"]:
        legs = [[raw(p) if p else None for p in leg] for leg in r["pts"]]
        steps = [dict(s=st["start"], t=st["text"], a=raw(st["a"])) for st in r["steps"]]
        routes.append(dict(name=r["name"], len=r["len"], sweep=bool(r.get("sweep")), legs=legs, steps=steps))
    markers = [dict(k="entrance", n=e["n"], p=raw(e["p"]), c=f"{e['c']:08X}") for e in data["ents"]]
    markers += [dict(k=n.get("k", "goal"), n=n["n"], p=raw(n["p"]), c=f"{n['c']:08X}") for n in data["named"]]
    markers += [dict(k=o["k"], n=o["n"], p=raw(o["p"]), c=f"{o['c']:08X}") for o in data["objs"] if o["k"] != "door"]
    out = dict(lb=lb, slug=cfg["slug"], title=cfg["title"], subtitle=cfg.get("subtitle", ""), group=cfg.get("group", ""),
               bounds=dict(x0=round(x0, 2), y0=round(y0, 2), x1=round(x1, 2), y1=round(y1, 2), w=w, h=h, scale=round(s, 4)),
               bands=bands, routes=routes, markers=markers,
               spawns=[raw(p[:3]) + [p[3]] for p in data["spawns"]],
               mons=[dict(name=m["name"], level=m["level"], n=m["n"], target=m["target"]) for m in data["mons"]],
               steps=cfg.get("steps", []), notes=cfg.get("notes", []), facts=cfg.get("facts", []))
    with open(os.path.join(out_dir, f"{lb}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    write_mesh(os.path.join(out_dir, f"{lb}.mesh"), CX, CY, floor, wall or [])
    return dict(lb=lb, slug=cfg["slug"], title=cfg["title"], group=cfg.get("group", ""), bands=len(bands), w=w, h=h)


def write_mesh(path, CX, CY, floor, wall):
    """The 3D view's geometry: "DMS1", float32 CX and CY, int32 floor and wall triangle counts, then int16
    centimetres per vertex in dunmap's three.js frame (x - CX, z, -(y - CY)), floors first."""
    with open(path, "wb") as f:
        f.write(b"DMS1" + struct.pack("<ffii", CX, CY, len(floor) // 9, len(wall) // 9))
        f.write(struct.pack(f"<{len(floor)}h", *floor))
        f.write(struct.pack(f"<{len(wall)}h", *wall))
