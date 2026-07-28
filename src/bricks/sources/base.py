"""The contract every offer source implements.

Adding a source should mean writing one file in this package and registering
it. Nothing else in the codebase changes — that is the whole point of the
abstraction, and the reason `services/` never names a source directly.

Note that the catalogue clients in this package (`rebrickable`, `brickset`)
are not Sources: they answer "what does this set weigh, cost, look like",
not "what is on sale right now".
"""

from typing import Protocol, runtime_checkable

from bricks.sources.models import RawOffer


@runtime_checkable
class Source(Protocol):
    """Answers one question: what is on promotion at the moment?

    Persists nothing, and does not know what a LEGO set is.
    """

    name: str

    def fetch(self) -> list[RawOffer]: ...
