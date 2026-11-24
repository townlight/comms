"""Fail if Townlight Communications imports CivicCore placeholder packages."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDERS = {
    "auth",
    "rbac",
    "audit",
    "ingestion",
    "search",
    "notifications",
    "connectors",
    "exemptions",
    "onboarding",
    "catalog",
    "verification",
}

SOURCE_ROOT = Path("townlight_comms")
PATTERN = re.compile(r"^\s*(?:from|import)\s+townlight_core\.([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)


def main() -> int:
    failures: list[str] = []
    scanned = 0
    for path in SOURCE_ROOT.rglob("*.py"):
        scanned += 1
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            package = match.group(1)
            if package in PLACEHOLDERS:
                failures.append(
                    f"{path}: townlight_core.{package} is a placeholder package in v1.3.0.dev2. "
                    "See AGENTS.md section 3.1."
                )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"PLACEHOLDER-IMPORT-CHECK: PASSED ({scanned} source files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
