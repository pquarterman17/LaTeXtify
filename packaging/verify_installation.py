"""Verify an extracted editable-offline LaTeXtify repository."""

from __future__ import annotations

import sys
from pathlib import Path

from latextify.integrity import format_result, verify_manifest


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        result = verify_manifest(root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: could not read the integrity manifest: {exc}")
        return 2
    print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
