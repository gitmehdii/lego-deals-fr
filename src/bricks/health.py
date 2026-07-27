"""Entry point for `python -m bricks.health`. Real code lives in adapters/cli."""

from bricks.adapters.cli.health import main

if __name__ == "__main__":
    raise SystemExit(main())
