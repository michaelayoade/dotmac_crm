#!/usr/bin/env bash
# Stop hook: soft reminder — run lint + type check on session-edited files.
#
# Reads the list of files edited during this session (tracked by
# post-edit-lint.sh) and runs ruff + mypy. Prints warnings to stderr
# but NEVER blocks — Claude sees the output and can choose to fix.
#
# Exit 0 always (non-blocking).
set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# ── Read tracked files from this session ────────────────────────
TRACK_DIR="/tmp/.claude_hooks_$(echo "$PROJECT_DIR" | md5sum | cut -d' ' -f1)"
TRACK_FILE="$TRACK_DIR/edited_files"

if [[ ! -f "$TRACK_FILE" ]]; then
    exit 0
fi

# Deduplicate and filter to Python files that still exist
FILES=""
while IFS= read -r f; do
    [[ -f "$f" && "$f" == *.py ]] && FILES="$FILES $f"
done < <(sort -u "$TRACK_FILE")

FILES=$(echo "$FILES" | xargs)
[[ -z "$FILES" ]] && exit 0

ISSUES_FOUND=0

# ── Ruff lint check ────────────────────────────────────────────
LINT_OUTPUT=$(cd "$PROJECT_DIR" && poetry run ruff check $FILES 2>&1) || {
    echo "--- ruff lint warnings in edited files ---" >&2
    echo "$LINT_OUTPUT" >&2
    ISSUES_FOUND=1
}

# ── mypy type check ────────────────────────────────────────────
MYPY_OUTPUT=$(cd "$PROJECT_DIR" && poetry run mypy $FILES --ignore-missing-imports --no-error-summary 2>&1) || {
    if echo "$MYPY_OUTPUT" | grep -q ": error:"; then
        echo "--- mypy type warnings in edited files ---" >&2
        echo "$MYPY_OUTPUT" >&2
        ISSUES_FOUND=1
    fi
}

if [[ $ISSUES_FOUND -eq 1 ]]; then
    echo "" >&2
    echo "Tip: consider fixing these before finishing." >&2
fi

# Always exit 0 — never block the stop
exit 0
