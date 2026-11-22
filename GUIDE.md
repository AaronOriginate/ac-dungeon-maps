# Making a dungeon map

This guide covers how a dungeon goes from raw game data to a finished map: finding it, reading what is inside it, writing its config, and building the web page and plugin data. Every map in this project was made this way.

One dungeon is one landblock. One config file in `mapgen/configs/` describes it, and the generator turns that config into a web page and plugin data.

## Raw data

| Source | What it holds | What the maps use |
| --- | --- | --- |
| `client_cell_1.dat` | Every indoor cell of every landblock | Each cell's room shape, position, rotation, its portals to neighbouring cells and the static objects in it |
| `client_portal.dat` | Shared game assets | Room shapes (Environment records, `0x0D......`), with vertices, polygons and which polygons are portals, plus the physics of static objects such as doors |
| ACE world data, `6 LandBlockExtendedData/<LB>.sql` | What is placed in each landblock | Monsters, generators, portals, doors, chests, levers and NPCs, with cell and position |
| ACE world data, `9 WeenieDefaults/` | One file per object type (weenie) | Names, levels, resistances, generator tables, portal destinations, lock codes, and NPC dialogue and rewards |
| ACE world data, `8 QuestDefDB/` | Quest flags | Repeat timers and how many times a quest can be done |
| `mapgen/weenies.json` | A compact index of the weenie files | Everything the generator needs from `9 WeenieDefaults`, so it doesn't parse 43,000 SQL files on every build |

The world data is ACE-World-16PY plus ACE-World-16PY-Patches. A file in the patches replaces the base file with the same id.

### Ids

- A cell id looks like `0x01F801D4`. The first four hex digits are the landblock (`01F8`) and the last four are the cell in it (`01D4`).
- Indoor cells start at `0x0100`. Anything below that is outdoors.
- Record `0xLLLLFFFE` in `client_cell_1.dat` is the landblock's info record, which says how many indoor cells it has. They are numbered from `0xLLLL0100` up.
- Positions are metres in the landblock's own frame: x east, y north, z up.

## Workspace

The game's dat files are not in this repository. The generator expects this layout:

```
workspace/
  ac-dungeon-maps/            this repository
  game-data/client-files/     client_cell_1.dat and client_portal.dat
  world-data/                 ACE-World-16PY and ACE-World-16PY-Patches
  clients/OpenAC/             an OpenAC checkout, only needed to build the plugin
  maps/                       web pages are written here
```

The commands below are run from the repository root.

## 1. Refresh the weenie index

Only needed when the world data changes.

```sh
python3 mapgen/tools/weenie_index.py
```

This rewrites `mapgen/weenies.json`. For each weenie it keeps the name, level, type, category (the top folder it sits in, such as Creature, Portal or Chest), resistances, generator table, portal destination and lock details.

## 2. Find the dungeon

Search the portals by name:

```sh
python3 mapgen/tools/find_dungeon.py "mite maze"
```

```
01F8  cell 0x01F801D4  pos [6.1, -101.6, 0.0]   1121 Mite Maze Portal  (mite-maze.json)
01F8  cell 0x01F8037E  pos [90.0, -60.0, 6.0]   6631 Portal to Mite Maze Wizard  (mite-maze.json)
```

The first column is the landblock. Each line is a portal that leads into the dungeon, with the cell and position it drops you at, and whether a config already covers it. Pass a landblock id instead of a name (`find_dungeon.py 01F8`) to list every way in.

If no portal matches, stand in the dungeon on an ACE server and type `/loc`. The cell it prints starts with the landblock.

## 3. Look inside

Make a minimal config and run the generator with `--info`:

```json
{"landblock": "01F8", "slug": "mite-maze", "title": "Mite Maze"}
```

```sh
python3 mapgen/tools/dunmap.py mapgen/configs/mite-maze.json --info
```

```
cells 879 levels [18, 12, 6, 0, -6]
entrances (portals into here):
   Mite Maze Portal 0x1f801d4 [6.1, -101.6, 0.0]
portal objects:
   Surface 0x1f801c0 [1.4, -91.4, 0.3] -> C7B20010
objects: {'Royal Mite Squire': 36, 'Royal Mite Sentry': 21, ..., "Branith's Staff": 1, 'Mite Warrior Queen': 1}
   Door Door 1292 0x1f80391 [104.7, -40.0, 6.0] LOCKED masterkey
spawns: {('Royal Mite Squire', 60): 36, ('Mite Warrior Queen', 60): 1, ...}
trap drops: [([0, -90, 6], '0x1f801c0')]
```

| Line | Meaning |
| --- | --- |
| `cells`, `levels` | How many indoor cells there are, and their heights |
| `entrances` | Portals elsewhere in the world that lead here. These become the route starts |
| `outside openings` | Doorways straight to the outdoors, for caves you walk into |
| `portal objects` | Portals inside the dungeon. `same lb` ones are used by routes automatically. Others lead out |
| `objects` | Everything placed here, by name. Route goals are picked from these names |
| Door, chest and lever lines | Each with its cell, position and lock code if it is locked |
| `spawns` | Monsters by name and level, including what generators make |
| `trap drops` | Holes and shafts that land you on a portal out of the dungeon. Routes avoid them |

## 4. Research the quest

The notes and steps on a map come from the world data, not from memory.

- **Who gives the quest.** Find the NPC's weenie file in `9 WeenieDefaults` and read its emote table: its `Tell` lines, the items it refuses or accepts, what it gives back, and the quest flags it sets and checks.
- **Repeat timers.** Look the quest flag up in `8 QuestDefDB`. The second value is the wait before it can be done again, in seconds, and the third is how many times it can be done (-1 means no limit).
- **Where the item is.** If an item is placed in the dungeon, it is in `--info`'s object list. If a monster carries it, search the creature files for the item's id in a create list.
- **Best damage.** Each creature's `res` in `weenies.json` is the damage it takes by element. Below 1.0 means it resists.

For example, to find what the Mite Maze Warden says and rewards:

```sh
grep -rl "'fachubmitemaze'" ../world-data/ACE-World-16PY*/Database/*/9\ WeenieDefaults
```

Write down only what the data shows. If something is a guess, say so in the note, as in "(inferred)".

## 5. Write the config

`mapgen/configs/mite-maze.json`, shortened:

```json
{
  "landblock": "01F8",
  "slug": "mite-maze",
  "title": "Mite Maze",
  "subtitle": "Branith's Staff and Shirt, Mite Warrior Queen",
  "group": "Facility Hub 30-45",
  "entrances": [{"name": "Entrance", "portal": "Mite Maze Portal"}],
  "routes": [
    {"name": "To Branith's Staff", "from": "Entrance", "to": [{"instance": "Branith's Staff"}]},
    {"name": "To the Mite Warrior Queen", "from": "Entrance", "to": [{"instance": "Mite Warrior Queen"}]}
  ],
  "sweeps": [{"name": "Royal Mite sweep", "from": "Entrance", "creatures": ["Royal Mite Squire", "Royal Mite Matron"]}],
  "targets": ["Mite Warrior Queen", "Royal Mite Matron", "Royal Mite Squire"],
  "steps": ["In the Facility Hub, ask the Warden about the Mite Maze (level 30+).", "Take the Mite Maze portal."],
  "facts": [["Hub portal", "level 30+"]],
  "notes": [{"kind": "warn", "title": "Trap drops", "body": "Both Surface portals drop you outdoors at the Lost Wish Mountains."}]
}
```

| Key | Use |
| --- | --- |
| `landblock`, `slug`, `title` | Required. The slug is the page's file name |
| `subtitle`, `group` | Shown on the page and used to group maps on the index |
| `entrances` | `{"portal": NAME}` uses a portal from `--info`. `{"cell": "0x...", "pos": [x, y, z]}` sets one by hand. `{"outside": true}` uses a doorway to the outdoors. Leave it out to use every portal into the dungeon |
| `routes` | A start and a list of goals walked in order. A goal is `{"instance": NAME}` for an object from `--info`, or `{"cell": ..., "pos": ..., "label": ...}` for any point |
| `pick`, `index` | When several objects share a name: `nearest`, a compass direction (`north`, `southwest` and so on), `up`, `down`, or an index into the list |
| `sweeps` | A route that visits every spawn of the named creatures, for kill tasks |
| `targets` | Creatures to highlight in red |
| `steps`, `facts`, `notes` | Quest steps, stat tiles, and notes. Note kinds are `info`, `tip` and `warn` |
| `climb_up` | Set to `true` when the stairs are separate objects rather than sloped floors, so the router can climb them |

## 6. Build and check the web page

```sh
python3 mapgen/tools/dunmap.py mapgen/configs/mite-maze.json
python3 mapgen/tools/dunmap_index.py
```

The first command writes `maps/mite-maze.html` and a summary in `maps/_summaries/`. The second rebuilds `maps/index.html` and `maps/nav.js` from all the summaries. Open the page and check:

- Every route reaches its goal. `NO ROUTE to ...` means a goal can't be reached from that start. Try another entrance, `climb_up`, or a `pick`.
- `no instance named ...` means the goal's name doesn't match anything in `--info`'s object list.
- Routes don't drop through holes you didn't expect, and step text such as "Walk 35 m east to the way down" makes sense.
- Locked doors and trap drops have a note.

## 7. Export to the plugin and test in game

```sh
python3 mapgen/tools/export_all.py
./build.sh
```

`export_all.py` writes every config's plugin data to `plugin/data/`: `<LB>.json` with routes, markers and notes, one floor image per 6 m level band, and `<LB>.mesh` for the 3D view. `build.sh` compiles the plugin.

To test, load the plugin in OpenAC on a local ACE server, run `/dmap goto <dungeon>` as an admin and walk each route. Check the line on the floor follows real corridors and the footer counts down.

For the public site, copy `maps/` into this repository's `docs/` folder.

## How the generator works

| Step | What happens |
| --- | --- |
| Reading the dats | `acdat.py` walks each dat's directory tree to find records by id, then decodes EnvCell records (cell to environment, position, portals, statics) and Environment records (room shapes) |
| Placing rooms | Each cell's room shape is rotated and moved into the landblock frame. Faces facing up become floors and the rest become walls |
| Linking rooms | Each portal polygon becomes a link to the cell on the other side. Upright ones are doorways. Flat ones are a way up or down, and become a ramp if a sloped floor continues through them |
| Drops and jumps | A flat portal with no ramp is a drop. Falling continues down to the first floor. A cell with no floor is a pit, which can be jumped if the far side is 12 m or less away, at a high cost so routes only jump when they must |
| Teleports | Portal objects whose destination is in the same landblock are links too. Portals that lead out are left out of routes |
| Routes | A shortest-path search over those links, from the start to each goal in turn |
| Sweeps | Spawns of the target creatures are grouped (within 14 m), then visited nearest first |
| Spawns | Monsters placed directly, plus what each generator table can make, including generators inside generators |
| Levels | Floor heights more than 1.5 m apart count as separate levels |

## Problems

| Problem | Fix |
| --- | --- |
| `No landblock record for LLLL` | The landblock has no indoor cells in your `client_cell_1.dat`. Check the id with `find_dungeon.py` |
| `no portal NAME` | Use a portal name exactly as `--info` lists it, or set the entrance with `cell` and `pos` |
| `skipped unreadable cells` | A few room shapes failed to decode. The map leaves those rooms out |
| A route goes the long way round | The direct way may be a locked door or need a jump. Check the door lines in `--info` |
| A monster is missing | It may only come from a generator in another landblock, or from a quest. Check its weenie file |
