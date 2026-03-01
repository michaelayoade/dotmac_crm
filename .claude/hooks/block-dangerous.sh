#!/usr/bin/env bash
# PreToolUse hook: block destructive bash commands before execution.
#
# Reads the Bash tool_input from stdin JSON (via jq).
# Checks for dangerous patterns and exits 2 to block them.
#
# Exit 0 = allow, exit 2 = command blocked (reason shown to Claude).
set -uo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[[ -z "$COMMAND" ]] && exit 0

# ── Destructive git operations ──────────────────────────────────

if echo "$COMMAND" | grep -qiE 'git\s+push\s+.*(--force|-f\b)'; then
    echo "BLOCKED: git push --force can overwrite remote history. Use regular push or ask the user first." >&2
    exit 2
fi

if echo "$COMMAND" | grep -qiE 'git\s+reset\s+--hard'; then
    echo "BLOCKED: git reset --hard discards all uncommitted changes. Ask the user first." >&2
    exit 2
fi

if echo "$COMMAND" | grep -qiE 'git\s+clean\s+-[a-zA-Z]*f'; then
    echo "BLOCKED: git clean -f permanently deletes untracked files. Ask the user first." >&2
    exit 2
fi

if echo "$COMMAND" | grep -qiE 'git\s+checkout\s+\.\s*$'; then
    echo "BLOCKED: git checkout . discards all unstaged changes. Ask the user first." >&2
    exit 2
fi

if echo "$COMMAND" | grep -qE 'git\s+branch\s+-D\s'; then
    echo "BLOCKED: git branch -D force-deletes a branch. Use -d for safe delete or ask the user." >&2
    exit 2
fi

# ── Destructive file operations ─────────────────────────────────

if echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+(/|~|\.\s*$)'; then
    echo "BLOCKED: rm -rf on root/home/current directory is too dangerous." >&2
    exit 2
fi

# ── Database destructive operations ─────────────────────────────

if echo "$COMMAND" | grep -qiE '\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+)'; then
    echo "BLOCKED: DROP/TRUNCATE destroys data permanently. Ask the user first." >&2
    exit 2
fi

exit 0
