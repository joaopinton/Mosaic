import json

import httpx
import pytest

from mosaic.llm import captions
from mosaic.llm.captions import BackendDown, RateLimited, build_payload, describe

GOOD = {"title": "Titolo", "caption": "Una frase.", "keywords": ["a", "b"]}
IMAGE = b"\xff\xd8jpeg"


def ok() -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- payload e intestazioni -------------------------------------------------------------------------------

def test_cloud_payload_names_the_model_and_has_no_grammar_schema():
    body = build_payload(IMAGE, "it", model="google/gemma-4-31b-it", schema=False)
    assert body["model"] == "google/gemma-4-31b-it"
    assert "response_format" not in body


def test_local_payload_is_unchanged():
    body = build_payload(IMAGE, "it")
    assert "model" not in body and body["response_format"]["type"] == "json_schema"


def test_cloud_request_carries_the_key_and_the_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return ok()

    result = describe("https://integrate.api.nvidia.com", IMAGE, client=client(handler), cloud=True,
                      model="google/gemma-4-31b-it", headers={"Authorization": "Bearer nvapi-x"})
    assert result.title == "Titolo"
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["auth"] == "Bearer nvapi-x"
    assert seen["body"]["model"] == "google/gemma-4-31b-it" and "response_format" not in seen["body"]


# --- errori del cloud -------------------------------------------------------------------------------------

def test_429_raises_rate_limited_with_retry_after_and_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "17"})

    with pytest.raises(RateLimited) as err:
        describe("http://x", IMAGE, client=client(handler), cloud=True)
    assert err.value.retry_after == 17 and len(calls) == 1


@pytest.mark.parametrize("header,expected", [(None, captions.DEFAULT_RETRY_AFTER), ("boh", captions.DEFAULT_RETRY_AFTER),
                                             ("0", 1), ("999999", captions.MAX_RETRY_AFTER)])
def test_retry_after_is_parsed_defensively(header, expected):
    headers = {"Retry-After": header} if header else {}
    with pytest.raises(RateLimited) as err:
        describe("http://x", IMAGE, client=client(lambda r: httpx.Response(429, headers=headers)), cloud=True)
    assert err.value.retry_after == expected


@pytest.mark.parametrize("status", [401, 402, 403, 404, 500, 502, 503])
def test_cloud_auth_and_server_errors_mean_backend_down_without_retry(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(BackendDown) as err:
        describe("http://x", IMAGE, client=client(handler), cloud=True)
    assert err.value.status == status and len(calls) == 1
    assert err.value.fatal == (status < 500)


def test_cloud_timeout_is_short_and_means_backend_down_without_retry():
    calls = []

    def handler(request):
        calls.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(BackendDown) as err:
        describe("http://x", IMAGE, client=client(handler), cloud=True)
    assert err.value.status == 0 and not err.value.fatal and "60 s" in str(err.value)
    assert calls == [captions.CLOUD_TIMEOUT]


def test_local_timeout_keeps_the_long_limit_and_is_retried():
    calls = []

    def handler(request):
        calls.append(request.extensions["timeout"]["read"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        return ok()

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=captions.REQUEST_TIMEOUT) as http:
        assert describe("http://x", IMAGE, client=http).title == "Titolo"
    assert calls == [captions.REQUEST_TIMEOUT] * 2


def test_local_server_errors_are_still_retried_with_the_strict_prompt():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500) if len(calls) == 1 else ok()

    assert describe("http://x", IMAGE, client=client(handler)).title == "Titolo"
    assert len(calls) == 2


def test_cloud_bad_json_is_retried_once_like_the_local_one():
    answers = iter(["non json", json.dumps(GOOD)])

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": next(answers)}}]})

    assert describe("http://x", IMAGE, client=client(handler), cloud=True).caption == "Una frase."
