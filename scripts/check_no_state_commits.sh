#!/usr/bin/env bash
# P2-2: Check that no runtime state files are being committed.
# Run as a CI step or pre-push hook.
#
# Usage: bash scripts/check_no_state_commits.sh
#
# Exit 0 if clean, exit 1 if state files detected in staged changes.

set -euo pipefail

STATE_PATTERNS=(
    "models/*.jsonl"
    "models/*.json"
    "models/*.csv"
)

# Check staged changes
if git diff --cached --name-only | grep -qE '^models/.*\.(jsonl|json|csv)$'; then
    echo "❌ ERROR: Runtime state files detected in staged changes (P2-2)."
    echo "   State files should NOT be committed to git."
    echo "   Use models/samples/ for schemas and models/archive/ for old data."
    echo ""
    echo "   Staged state files:"
    git diff --cached --name-only | grep -E '^models/.*\.(jsonl|json|csv)$' || true
    echo ""
    echo "   To unstage: git reset HEAD models/<file>"
    exit 1
fi

echo "✅ No runtime state files in staged changes."
exit 0
