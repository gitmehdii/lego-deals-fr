"""The one HTTP policy every source obeys.

We are a small project consuming other people's work, so the rules from
CLAUDE.md live here rather than being retyped in each client: an honest
identity, an explicit timeout, at most three attempts with exponential
backoff, and a polite surrender instead of hammering.
"""

import time
from collections.abc import Callable
from types import TracebackType

import httpx

from bricks.log import get_logger, redact_secrets

# Constant, not configurable. An honest identity is not a knob: anyone we
# inconvenience must be able to find out who we are and tell us to stop.
USER_AGENT = "bricks/0.1 (+https://github.com/gitmehdii/lego-deals-fr)"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0

_log = get_logger(__name__)


class SourceUnavailableError(Exception):
    """The remote failed us, or told us to back off. The run stops here.

    Never carries an unredacted message: it is built from `redact_secrets`
    output, because the URL that failed may well carry a credential.
    """


def build_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        transport=transport,
        follow_redirects=True,
    )


class HttpFetcher:
    """An httpx client wrapped in the retry policy above.

    `sleep` is injected so tests exercise the backoff without waiting for it.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client if client is not None else build_client()
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep

    def get(self, url: str) -> httpx.Response:
        return self._request("GET", url)

    def post(self, url: str, *, data: dict[str, str]) -> httpx.Response:
        """Form-encoded POST.

        Used rather than GET whenever a request carries a credential: a query
        parameter ends up inside every httpx exception message, a form body
        does not.
        """
        return self._request("POST", url, data=data)

    def post_json(self, url: str, *, json: object) -> httpx.Response:
        """JSON POST, which is what a Discord webhook expects."""
        return self._request("POST", url, json=json)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _request(
        self,
        method: str,
        url: str,
        data: dict[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.request(method, url, data=data, json=json)
            except httpx.HTTPError as exc:
                self._retry_or_surrender(attempt, url, redact_secrets(exc))
                continue

            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                # Not retried, whatever the attempt count: 429 is the server
                # telling us to stop. We come back at the next run.
                raise SourceUnavailableError(
                    f"rate limited by {redact_secrets(url)}, abandoning the run"
                )

            if response.is_server_error:
                self._retry_or_surrender(
                    attempt, url, f"server error {response.status_code}"
                )
                continue

            if response.is_client_error:
                # A 401 or a 404 will not fix itself in two seconds.
                raise SourceUnavailableError(
                    f"{redact_secrets(url)} returned {response.status_code}"
                )

            return response

        # Only reachable if max_attempts was set below 1, i.e. never try.
        raise SourceUnavailableError(
            f"{redact_secrets(url)} was never requested: max_attempts="
            f"{self._max_attempts}"
        )

    def _retry_or_surrender(self, attempt: int, url: str, reason: str) -> None:
        if attempt >= self._max_attempts:
            raise SourceUnavailableError(
                f"{redact_secrets(url)} failed after {attempt} attempts: {reason}"
            )
        delay = self._backoff_base_seconds * 2 ** (attempt - 1)
        _log.warning(
            "http_retry", url=url, attempt=attempt, delay_seconds=delay, reason=reason
        )
        self._sleep(delay)
