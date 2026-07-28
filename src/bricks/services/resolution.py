"""Bridging the catalogue in the database to the pure resolver in `core/`.

`core.resolve` knows nothing about SQLAlchemy, which is what makes it testable
against a fixture instead of a 27 810-row table. This module is the only place
that turns one into the other.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from bricks.core.resolve import CatalogueEntry, SetIndex
from bricks.db.models import Set
from bricks.log import get_logger

_log = get_logger(__name__)


def load_set_index(session: Session) -> SetIndex:
    """Read the catalogue once per run.

    Loaded whole and on purpose: resolution touches every offer, and 27 810
    rows of two short columns cost a few megabytes against one query.
    """
    entries = [
        CatalogueEntry(set_num=set_num, name_normalized=name)
        for set_num, name in session.execute(
            select(Set.set_num, Set.name_normalized)
        ).all()
    ]
    index = SetIndex(entries)
    if not entries:
        # Not fatal: offers are still worth storing. But nothing will resolve,
        # and silence here would look like a resolver that stopped working.
        _log.warning("catalogue_empty_resolution_disabled")
    return index
