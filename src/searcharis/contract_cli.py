"""Offline SEO contract check: python -m searcharis.contract_cli CONTRACT PAGE."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import verify_contract

_MAX_INPUT_BYTES = 4_000_000


def _read(path: Path) -> str:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError(f"File exceeds {_MAX_INPUT_BYTES} bytes: {path}")
    return path.read_text(encoding="utf-8")


def _unique_contract_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate contract JSON key: {key!r}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, read-only SEO contract assertions")
    parser.add_argument("contract", type=Path, help="JSON contract file")
    parser.add_argument("html", type=Path, help="Built HTML file; never fetched from the network")
    parser.add_argument("--json", action="store_true", help="Print machine-readable findings")
    args = parser.parse_args(argv)
    try:
        try:
            contract = json.loads(_read(args.contract), object_pairs_hook=_unique_contract_keys)
        except RecursionError as error:
            # Small, deeply nested JSON can exhaust the decoder stack.
            raise ValueError("Contract JSON exceeds supported nesting depth") from error
        report = verify_contract(contract, _read(args.html))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["status"] == "pass":
        print(f"PASS {report['checked_url']}")
    else:
        print(f"FAIL {report['checked_url']}")
        for item in report["issues"]:
            print(f"  {item['code']}: expected {item['expected']!r}, observed {item['actual']!r}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
