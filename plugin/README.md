# Dungeon Maps for OpenAC

An OpenAC plugin that shows the dungeon maps from `../maps` in game. In a mapped dungeon it opens a map window and draws the chosen route on the floor.

## In game

| Control | Effect |
| --- | --- |
| Alt+M or `/dmap` | Show or hide the map. It opens by itself when you enter a mapped dungeon |
| `3D` in the title bar, `/dmap 3d`, `/dmap 2d` | Switch between the 3D map (the default) and the flat map of one level |
| Arrows at the foot of the map, `/dmap next`, `/dmap route [n]` | Change route |
| Arrows in the title bar, Shift+wheel | Show another level. The map follows your level again when you change floor |
| Drag the map, right-drag, wheel | 3D: turn and tilt, slide, zoom. 2D: drag to pan, wheel to zoom. Right-click to follow you again |
| Drag the title bar | Move the window |
| `/dmap info` | Quest steps and notes for this dungeon, in chat |
| `/dmap lines on`, `/dmap lines off` | Route on the floor |
| `/dmap size small`, `medium`, `large`, `/dmap reset` | Window size, or put the window back |
| `/dmap list` | Every mapped dungeon |
| `/dmap goto <dungeon>` | Admin only: teleport to its entrance with ACE's `@teleloc` (for the local sandbox) |
| `/dmap tp <marker>` | Admin only: teleport to a goal, portal, lever or chest in this dungeon |

The 3D map looks like the web map: floors shaded by height, see-through walls, levels stretched apart. Levels above the one shown are cut away so you can see it from above, and levels below are dimmed. OpenAC's plugin canvas only draws 2D, so the plugin renders the 3D view itself on a background thread and shows it as an image.

The window shows the level you're on, the route (bright ahead, dim behind, faint on other levels), goals, entrances, exit and teleport portals, locked doors, levers, chests, traps, monster spawns (task targets in red) and you as an arrow. The footer shows the step you're on and the distance left.

OpenAC's own Map tab can't show plugin maps yet (its map registry is a stub in 0.1.32), so the map is a plugin window.

## Build and install

```sh
./build.sh          # build and install into ../sandbox/openac-data/plugins
./build.sh --maps   # re-export the map data from mapgen/configs first
```

The build runs the .NET 10 SDK in Docker against `../clients/OpenAC`. The sandbox mounts `../sandbox/openac-data` as the client's data folder, so `openac-data/plugins` is its plugins folder, and the client reloads the plugin about a second after the files change. To install it in a desktop OpenAC, copy `build/out` into the plugins folder as `ac.dungeon-maps` (`~/Library/Application Support/OpenAC/plugins` on macOS).

## Folders

| Folder | Contents |
| --- | --- |
| `plugin/` | The plugin (C#), its data (`data/<landblock>.json` and one floor image per level, and `<landblock>.mesh` for the 3D map) and marker art |
| `mapgen/` | The map generator: `tools/dunmap.py` builds the web maps, `tools/export_all.py` the plugin data, from the configs in `configs/` |
| `build/out/` | The built plugin |

Map data comes from the client dats in `../game-data/client-files` and the world data in `../world-data`. Positions are in the landblock's own frame, the same one OpenAC uses for `PluginDungeonFloorplan`.
