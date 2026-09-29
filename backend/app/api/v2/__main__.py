"""Launch the CPU-only Lab API without importing legacy GPU/ORM services."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .runtime import create_lab


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="V2 SQLite/NPZ storage directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8022)
    args = parser.parse_args()
    uvicorn.run(create_lab(args.root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
