"""Seed the database with baseline data.

Creates one institution, one admin user, one demo group, and one demo topic so
the UI team always has a working local environment. Safe to re-run: uses
"get or create" semantics.

Full implementation is completed as modules come online (Stage 4). This file
establishes the entry point and the CLI contract now so it's referenced by
the Makefile and README from day one.
"""

from __future__ import annotations

import asyncio
import sys


async def seed() -> int:
    """Populate the baseline dataset. Returns a process exit code."""
    # Stage 0 scaffold. Fully implemented during module builds (Stages 4a–4f)
    # once user / group / message services are available.
    print("seed: scaffold only — fill in as each module lands")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed()))
