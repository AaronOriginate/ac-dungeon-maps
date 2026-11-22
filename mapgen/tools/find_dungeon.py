"""Find a dungeon's landblock and entrances from a portal name, or list the portals into a landblock.

Usage:
  python3 find_dungeon.py "mite maze"      portals whose name contains the text, and where they lead
  python3 find_dungeon.py 01F8             every portal into landblock 01F8

Each line shows the landblock, the entrance cell and position (the "cell" and "pos" an entrance or route
in a config can use), the portal's weenie id and name, and the config that already maps it, if any.
Only destinations inside a dungeon (cell 0x0100 and up) are listed; outdoor portals are skipped.
"""
import json
import re
import sys
from pathlib import Path

MAPGEN = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    q = sys.argv[1].strip()
    with (MAPGEN / "weenies.json").open(encoding="utf-8") as f:
        W = json.load(f)
    mapped = {}
    for p in sorted((MAPGEN / "configs").glob("*.json")):
        cfg = json.loads(p.read_text(encoding="utf-8"))
        mapped.setdefault(cfg["landblock"].upper(), []).append(p.name)
    by_lb = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]{4})", q)
    rows = []
    for wcid, w in W.items():
        d = w.get("dest")
        if w.get("cat") != "Portal" or not d or d[0] & 0xFFFF < 0x100:
            continue
        lb = f"{d[0] >> 16:04X}"
        if (by_lb and lb == by_lb.group(1).upper()) or (not by_lb and q.lower() in w["name"].lower()):
            rows.append((lb, d[0], d[1:], int(wcid), w["name"]))
    if not rows:
        raise SystemExit("no dungeon portal matches " + repr(q))
    for lb, cell, pos, wcid, name in sorted(rows):
        have = ", ".join(mapped.get(lb, [])) or "not mapped yet"
        print(f"{lb}  cell 0x{cell:08X}  pos [{pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}]  {wcid:>5} {name}  ({have})")


if __name__ == "__main__":
    main()
