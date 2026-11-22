"""Export all configured dungeons and their index to the plugin data folder."""
import json
import os
from pathlib import Path

import dunmap


def main():
    root = Path(__file__).resolve().parents[2]
    out = root / "plugin" / "data"
    configs = sorted((root / "mapgen" / "configs").glob("*.json"))
    if not configs:
        raise SystemExit("No dungeon configs found")
    os.environ["DUNMAP_GAME"] = str(out)
    rows = [dunmap.build(json.loads(path.read_text(encoding="utf-8"))) for path in configs]
    rows.sort(key=lambda r: (r["group"], r["title"]))
    with (out / "index.json").open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=0)
    print(len(rows), "maps exported to", out)


if __name__ == "__main__":
    main()
