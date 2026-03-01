#!/usr/bin/env bash
# PostToolUse hook: auto-format Python files and validate route patterns.
#
# Runs after every Edit/Write on *.py files.
# 1. ruff check --fix  (auto-fix lint, best-effort)
# 2. ruff format        (auto-format, best-effort)
# 3. check_route_logic  (AST-based route validation, exit 2 on violation)
# 4. Track edited file for the Stop quality-gate hook
#
# Exit 0 = clean, exit 2 = violation found (message shown to Claude).
set -uo pipefail

FILE_PATH="${CLAUDE_FILE_PATH:-}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# Skip if no file path, not Python, or file doesn't exist
[[ -z "$FILE_PATH" ]] && exit 0
[[ "$FILE_PATH" != *.py ]] && exit 0
[[ ! -f "$FILE_PATH" ]] && exit 0

# --- Auto-format (silent, best-effort — never blocks) ---
poetry run ruff check "$FILE_PATH" --fix --quiet 2>/dev/null || true
poetry run ruff format "$FILE_PATH" --quiet 2>/dev/null || true

# --- Route logic validation (blocks on violation) ---
OUTPUT=$(python3 "$PROJECT_DIR/scripts/check_route_logic.py" "$FILE_PATH" 2>&1) || {
    echo "$OUTPUT" >&2
    exit 2
}

# --- Track this file for the Stop quality-gate hook ---
TRACK_DIR="/tmp/.claude_hooks_$(echo "$PROJECT_DIR" | md5sum | cut -d' ' -f1)"
mkdir -p "$TRACK_DIR" 2>/dev/null || true
echo "$FILE_PATH" >> "$TRACK_DIR/edited_files" 2>/dev/null || true

exit 0
