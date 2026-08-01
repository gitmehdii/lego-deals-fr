import httpx
import pytest

from bricks.sources.http import USER_AGENT, HttpFetcher, SourceUnavailableError


def _fetcher(handler, **kwargs) -> HttpFetcher:
    """A fetcher that never actually sleeps, whatever the backoff says."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )
    kwargs.setdefault("sleep", lambda _: None)
    return HttpFetcher(client, **kwargs)


def test_sends_an_honest_user_agent_with_a_contact_url():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        return httpx.Response(200, content=b"ok")

    _fetcher(handler).get("https://example.test/dump")
    assert seen["ua"] == USER_AGENT
    assert "https://" in USER_AGENT


def test_returns_the_response_on_success():
    handler = lambda request: httpx.Response(200, content=b"payload")  # noqa: E731
    assert _fetcher(handler).get("https://example.test/dump").content == b"payload"


def test_rate_limit_abandons_immediately_without_retrying():
    """429 is the server telling us to stop. Retrying is exactly the wrong move."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    with pytest.raises(SourceUnavailableError, match="rate limited"):
        _fetcher(handler).get("https://example.test/dump")
    assert len(calls) == 1


def test_server_error_is_retried_up_to_the_attempt_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(SourceUnavailableError, match="after 3 attempts"):
        _fetcher(handler).get("https://example.test/dump")
    assert len(calls) == 3


def test_server_error_that_clears_is_not_an_error():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500 if len(calls) == 1 else 200, content=b"late")

    assert _fetcher(handler).get("https://example.test/dump").content == b"late"
    assert len(calls) == 2


def test_transport_failure_is_retried_then_abandoned():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(SourceUnavailableError, match="after 3 attempts"):
        _fetcher(handler).get("https://example.test/dump")
    assert len(calls) == 3


def test_client_error_is_not_retried():
    """A 401 or a 404 will not fix itself in two seconds."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    with pytest.raises(SourceUnavailableError, match="404"):
        _fetcher(handler).get("https://example.test/dump")
    assert len(calls) == 1


def test_backoff_is_exponential():
    delays = []

    def handler(request):
        return httpx.Response(500)

    fetcher = _fetcher(handler, sleep=delays.append, backoff_base_seconds=1.0)
    with pytest.raises(SourceUnavailableError):
        fetcher.get("https://example.test/dump")
    assert delays == [1.0, 2.0]


def test_post_sends_a_form_body_and_leaves_the_url_clean():
    """Credentials travel in the body so they never reach an exception message."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b"ok")

    _fetcher(handler).post("https://example.test/api", data={"apiKey": "s3cret"})
    assert "s3cret" not in seen["url"]
    assert "apiKey=s3cret" in seen["body"]


def test_failure_message_never_carries_a_credential():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(SourceUnavailableError) as excinfo:
        _fetcher(handler).get("https://example.test/api?authToken=s3cret")
    assert "s3cret" not in str(excinfo.value)
