#!/usr/bin/env bash
# install-local.sh — copy this evercode project into a Claude Code skills directory.
#
# Usage:
#   ./install-local.sh                            # → ~/.claude/skills/evercode
#   ./install-local.sh ~/proj/.claude/skills      # → that dir's evercode/  (per-project)
#   ./install-local.sh --dry-run [target]         # show what would be copied, write nothing
#   ./install-local.sh -h | --help
#
# Dereferences symlinks (skills/evercode/{SKILL,INVARIANTS}.md → root) so the
# install is self-contained. Excludes dev/runtime junk (.git, .codegraph,
# .DS_Store, __pycache__, .evercode, proxy.log, proxy.pid). Re-runnable: an
# existing evercode/ at the target is replaced.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_TARGET="$HOME/.claude/skills"

usage() {
  cat <<'EOF'
install-local.sh — copy evercode into a Claude Code skills directory.

Usage:
  ./install-local.sh                            # → ~/.claude/skills/evercode
  ./install-local.sh <skills-dir>               # → <skills-dir>/evercode
  ./install-local.sh --dry-run [target]         # show what would be copied
  ./install-local.sh -h | --help

The install dereferences symlinks so it's self-contained, and excludes
.git/.codegraph/.DS_Store/__pycache__/.evercode/proxy.(log|pid).
EOF
  exit 0
}

DRY=0
TARGET=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) usage ;;
    -*) echo "install-local: unknown option '$a'" >&2; exit 2 ;;
    *) [ -n "$TARGET" ] && { echo "install-local: unexpected extra arg '$a'" >&2; exit 2; }
       TARGET="$a" ;;
  esac
done
TARGET="${TARGET:-$DEFAULT_TARGET}"
DEST="${TARGET%/}/evercode"

if ! command -v rsync >/dev/null 2>&1; then
  echo "install-local: 'rsync' not found (macOS: 'brew install rsync'). Aborting." >&2
  exit 1
fi

# Refuse to ship a broken skill: the two load-bearing files must resolve here.
for f in SKILL.md INVARIANTS.md; do
  if [ ! -f "$SRC/$f" ]; then
    echo "install-local: $SRC/$f missing — refusing to install a broken skill." >&2
    exit 1
  fi
done

echo "source : $SRC"
echo "target : $DEST"
[ "$DRY" = "1" ] && echo "(dry-run — nothing will be written)"

if [ "$DRY" = "0" ]; then
  if [ -e "$DEST" ]; then
    echo "existing install found at $DEST — replacing."
    rm -rf "$DEST"
  fi
  mkdir -p "$TARGET"
fi

RSYNC=(rsync -aL
  --exclude='.git/'
  --exclude='.codegraph/'
  --exclude='.DS_Store'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.evercode/'
  --exclude='proxy/proxy.log'
  --exclude='proxy/proxy.pid'
)
[ "$DRY" = "1" ] && RSYNC+=(--dry-run -v)

"${RSYNC[@]}" "$SRC/" "$DEST/"

echo
if [ "$DRY" = "0" ]; then
  echo "installed → $DEST"
  echo "restart Claude Code (or start a new session) to register the skill."

  # Expose the launcher on PATH. The launcher never changes cwd, so users can
  # run `evercode` from any project dir and CC starts there.
  BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
  ln -sf "$DEST/evercode" "$BIN_DIR/evercode"
  case ":$PATH:" in
    *":$BIN_DIR:"*)
      echo "launcher on PATH: run 'evercode' from any project dir." ;;
    *)
      echo "launcher symlinked at $BIN_DIR/evercode — add $BIN_DIR to PATH to run 'evercode' anywhere." ;;
  esac
else
  echo "(dry-run complete — nothing written)"
fi
