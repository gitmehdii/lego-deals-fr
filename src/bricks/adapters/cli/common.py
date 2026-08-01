"""Shared entry-point plumbing for the three commands."""

import sys

from pydantic import ValidationError

from bricks.config import Settings, get_settings
from bricks.log import configure_logging


def load_settings() -> Settings | None:
    """Settings, or None once the problem has been reported to the user.

    A misconfigured environment is a mistake, not a crash, and a traceback is
    a poor way to say "you pasted the wrong URL". The message pydantic built
    is already the useful part; the stack above it is noise.

    Nothing here prints a value: `hide_input_in_errors` keeps the offending
    input out of the error, which matters because most of these fields are
    credentials.
    """
    try:
        return get_settings()
    except ValidationError as error:
        print("configuration error\n", file=sys.stderr)
        for issue in error.errors():
            field = ".".join(str(part) for part in issue["loc"]).upper()
            message = issue["msg"].removeprefix("Value error, ")
            print(f"  {field}\n    {message}\n", file=sys.stderr)
        return None


def configure(settings: Settings) -> None:
    configure_logging(settings.log_level)
