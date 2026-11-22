#!/bin/bash
# Builds the Dungeon Maps plugin and installs it into the sandbox client's plugins folder.
# Usage: ./build.sh [--maps]
#   --maps  re-export the map data from mapgen/configs first (needs python3 with Pillow and numpy)
# The client reloads the plugin by itself about a second after the files change.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
if [ "${1:-}" = "--maps" ]; then python3 mapgen/tools/export_all.py; fi

# Compiled with the .NET 10 SDK in Docker, against the OpenAC checkout's plugin contract.
mkdir -p build
docker run --rm \
  -v "$PWD:/ws/openac-dungeon-maps" -v "$ROOT/clients/OpenAC:/ws/clients/OpenAC:ro" \
  -v ace-sandbox_dev-nuget:/root/.nuget/packages \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
  -w /ws/openac-dungeon-maps/plugin mcr.microsoft.com/dotnet/sdk:10.0-noble \
  bash -c 'dotnet build DungeonMaps.csproj -c Release -o /tmp/out --artifacts-path /tmp/artifacts -nologo -v q \
           && rm -rf /ws/openac-dungeon-maps/build/out && mkdir -p /ws/openac-dungeon-maps/build/out \
           && cp -r /tmp/out/AcDungeonMaps.* /tmp/out/plugin.json /tmp/out/data /tmp/out/art /ws/openac-dungeon-maps/build/out/'

# The host ships the contract assembly, so only the plugin's own files are installed.
DEST="$ROOT/sandbox/openac-data/plugins/ac.dungeon-maps"
mkdir -p "$DEST"
rsync -a --delete --exclude files build/out/ "$DEST/"
echo "installed to $DEST"
