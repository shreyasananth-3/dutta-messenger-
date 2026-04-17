"""Export the FastAPI OpenAPI spec to docs/ui-contract/openapi.json.

Run via `make openapi-export`. CI should run this and fail if the committed
snapshot drifts from what the current code generates, catching accidental
contract changes in review.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    """Write docs/ui-contract/openapi.json from the live FastAPI app."""
    # Import lazily so this script doesn't require a DB connection at import.
    try:
        from src.main import app
    except Exception as exc:  # pragma: no cover - surfaced to the human
        print(f"Failed to import FastAPI app: {exc}", file=sys.stderr)
        return 1

    output = Path("docs/ui-contract/openapi.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    spec = app.openapi()
    output.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
