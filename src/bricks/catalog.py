"""Entry point for `python -m bricks.catalog`. Real code lives in adapters/cli."""

from bricks.adapters.cli.catalog import main

if __name__ == "__main__":
    raise SystemExit(main())
