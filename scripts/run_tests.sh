#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# run_tests.sh — run the full test suite and archive a timestamped proof folder.
#
# Usage: ./scripts/run_tests.sh [pytest args...]
#
# Produces:
#   tests/results/latest/              <- always the most recent run
#   tests/results/YYYY-MM-DD_HHMMSS/   <- permanent archive of every run
#
# Each run folder contains:
#   junit.xml                pytest results (CI-consumable)
#   coverage.xml             cobertura coverage
#   coverage.json            machine-readable coverage
#   coverage-html/           browsable report
#   pytest-output.txt        full stdout
#   summary.md               short human report
# ----------------------------------------------------------------------------
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
LATEST_DIR="tests/results/latest"
ARCHIVE_DIR="tests/results/${TIMESTAMP}"

mkdir -p "$LATEST_DIR" "$ARCHIVE_DIR"

echo "==> Running test suite (results -> ${ARCHIVE_DIR})"

# pytest writes to latest/ via pyproject.toml; we tee stdout too.
set +e
pytest "$@" 2>&1 | tee "${LATEST_DIR}/pytest-output.txt"
PYTEST_EXIT=${PIPESTATUS[0]}
set -e

# Extract a short coverage summary the humans can skim.
if [ -f "${LATEST_DIR}/coverage.json" ]; then
    python3 - <<PY > "${LATEST_DIR}/summary.md"
import json, pathlib
data = json.loads(pathlib.Path("${LATEST_DIR}/coverage.json").read_text())
totals = data.get("totals", {})
print(f"# Test run — ${TIMESTAMP}")
print()
print(f"- Exit code: ${PYTEST_EXIT}")
print(f"- Line coverage: {totals.get('percent_covered', 0):.2f}%")
print(f"- Branch coverage: {totals.get('percent_covered_display', 'n/a')}%")
print(f"- Statements covered: {totals.get('covered_lines', 0)}/{totals.get('num_statements', 0)}")
print(f"- Branches covered: {totals.get('covered_branches', 0)}/{totals.get('num_branches', 0)}")
print()
print("## Per-module coverage")
for name, meta in sorted(data.get("files", {}).items()):
    pct = meta.get("summary", {}).get("percent_covered", 0)
    print(f"- `{name}`: {pct:.2f}%")
PY
else
    echo "# Test run — ${TIMESTAMP}" > "${LATEST_DIR}/summary.md"
    echo "" >> "${LATEST_DIR}/summary.md"
    echo "Coverage JSON not produced — see pytest-output.txt." >> "${LATEST_DIR}/summary.md"
fi

# Copy everything into the permanent archive folder.
cp -R "${LATEST_DIR}/." "${ARCHIVE_DIR}/"

echo "==> Done. Proof: ${ARCHIVE_DIR}/summary.md"
exit "$PYTEST_EXIT"
