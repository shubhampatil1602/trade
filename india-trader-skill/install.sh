#!/bin/sh
# Copy the india-* skills (and the shared india-lib) into Claude Code's skills folder.
#   ./install.sh            -> ~/.claude/skills/
#   ./install.sh --project  -> ./.claude/skills/ in the current directory
set -e
here="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--project" ]; then
  dest="$(pwd)/.claude/skills"
else
  dest="$HOME/.claude/skills"
fi
mkdir -p "$dest"
for d in "$here"/india-*; do
  name="$(basename "$d")"
  rm -rf "$dest/$name"
  cp -R "$d" "$dest/$name"
  find "$dest/$name" -name "__pycache__" -type d -prune -exec rm -rf {} +
  echo "installed $name"
done
echo "Done: $dest. Open a new Claude Code session to load them."
