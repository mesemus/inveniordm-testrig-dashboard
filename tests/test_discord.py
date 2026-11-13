import json

import httpx2
import pytest

from invenio_testrig_dashboard import discord

URL = "https://discord.com/api/webhooks/1/token"


def _transport(handler):
    return httpx2.MockTransport(handler)


def test_payload_suppresses_mentions_and_link_previews():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(204)

    assert discord.post(URL, "hello", transport=_transport(handler)) is True

    payload = json.loads(requests[0].content)
    assert payload["content"] == "hello"
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["flags"] == 4


def test_429_retries_once():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(429, json={"retry_after": 0})
        return httpx2.Response(204)

    assert discord.post(URL, "hi", transport=_transport(handler)) is True
    assert len(calls) == 2


def test_other_errors_return_false_instead_of_raising():
    assert (
        discord.post(URL, "hi", transport=_transport(lambda request: httpx2.Response(500))) is False
    )


def test_no_webhook_makes_no_http_call():
    def handler(request):
        pytest.fail("no HTTP call expected without a webhook")

    assert discord.post(None, "hi", transport=_transport(handler)) is True
