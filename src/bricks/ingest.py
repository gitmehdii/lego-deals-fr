"""Entry point for `python -m bricks.ingest`. Real code lives in adapters/cli."""

from bricks.adapters.cli.ingest import main

if __name__ == "__main__":
    raise SystemExit(main())
