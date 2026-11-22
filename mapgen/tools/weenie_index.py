"""Build mapgen/weenies.json: a compact index of every weenie in the ACE world data.

Usage: python3 weenie_index.py [OUT.json]

Reads ACE-World-16PY (base) and then ACE-World-16PY-Patches, so a patched weenie replaces the base one.
For each weenie it keeps only what the maps need:
  cls, wtype, cat   class name, weenie type and the top folder it sits in (Creature, Portal, Door, Chest...)
  name, level
  res               damage taken by element (Resist* float properties 64-70), creatures only
  gen               generator table rows: [probability, weenie, delay, init, max, where]
  dest              portal destination: [cell, x, y, z]
  locked, lockcode  for doors and chests
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
WD = TOOLS.parents[2] / "world-data"
BASES = [WD / "ACE-World-16PY/Database/3-Core/9 WeenieDefaults/SQL", WD / "ACE-World-16PY-Patches/Database/Patches/9 WeenieDefaults"]
RES = {64: "slash", 65: "pierce", 66: "bludgeon", 67: "fire", 68: "cold", 69: "acid", 70: "electric"}


def index_file(path, base):
    t = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"VALUES \((\d+), '([^']*)', (\d+),", t)
    if not m:
        return None, None
    d = dict(cls=m.group(2), wtype=int(m.group(3)), cat=os.path.relpath(path, base).split(os.sep)[0])
    n = re.search(r"\(\d+,\s+1, '((?:[^']|'')*)'\) /\* Name", t)
    d["name"] = n.group(1).replace("''", "'") if n else m.group(2)
    lv = re.search(r"\(\d+,\s+25,\s+(\d+)\) /\* Level \*/", t)
    d["level"] = int(lv.group(1)) if lv else None
    r = {RES[int(k)]: float(v) for k, v in re.findall(r"\(\d+,\s+(6[4-9]|70),\s+([-\d.]+)\) /\* Resist", t)}
    if r:
        d["res"] = r
    gtxt = t[t.find("weenie_properties_generator"):] if "weenie_properties_generator" in t else ""
    g = re.findall(r"\(\d+, (-?[\d.]+), (\d+), ([\d.]+), (\d+), (\d+), (\d+), (\d+),", gtxt)
    if g:
        d["gen"] = [[float(p), int(c), float(dl), int(i), int(mx), int(wh)] for p, c, dl, i, mx, _when, wh in g]
    pd = re.search(r"\(\d+, 2, (0x[0-9A-Fa-f]{8}), ([-\d.]+), ([-\d.]+), ([-\d.]+),", t)
    if pd:
        d["dest"] = [int(pd.group(1), 16), float(pd.group(2)), float(pd.group(3)), float(pd.group(4))]
    lk = re.search(r"\(\d+,\s+\d+,\s+(True|False)\s*\) /\* Locked \*/", t)
    if lk:
        d["locked"] = lk.group(1) == "True"
    ky = re.search(r"\(\d+,\s+\d+, '((?:[^']|'')*)'\) /\* LockCode", t)
    if ky:
        d["lockcode"] = ky.group(1)
    return int(m.group(1)), d


def main():
    out = {}
    for base in BASES:
        if not base.is_dir():
            raise SystemExit(f"missing world data folder: {base}")
        for f in glob.glob(str(base / "**" / "*.sql"), recursive=True):
            w, d = index_file(f, str(base))
            if w is not None:
                out[w] = d
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else TOOLS.parent / "weenies.json"
    with target.open("w", encoding="utf-8") as f:
        json.dump(out, f)
    print(len(out), "weenies written to", target)


if __name__ == "__main__":
    main()
