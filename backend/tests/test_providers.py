import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.providers import ProviderClient, ProviderError


TOOLS = [{"type": "function", "function": {
    "name": "get_team", "description": "Read the team",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}]
MESSAGES = [{"role": "system", "content": "Use read-only tools."},
            {"role": "user", "content": "Read the roster."}]


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def install(responses):
        queue = iter(responses)

        def handler(request):
            requests.append(request)
            response = next(queue)
            if isinstance(response, Exception):
                raise response
            return response

        def factory(**kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            assert kwargs["timeout"].connect == 10
            return original(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return requests
    return install


def grok_response(output=None, **extra):
    return httpx.Response(200, json={
        "status": "completed",
        "output": output if output is not None else [
            {"type": "message", "content": [{"type": "output_text", "text": "Ready"}]},
        ], "usage": {"input_tokens": 9, "output_tokens": 3}, **extra,
    })


def gemini_response(parts=None, **extra):
    return httpx.Response(200, json={
        "candidates": [{"finishReason": "STOP", "content": {
            "role": "model", "parts": parts if parts is not None else [{"text": "Ready"}],
        }, **extra}], "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 3},
    })


def test_grok_function_roundtrip(transport):
    requests = transport([
        grok_response([
            {"type": "reasoning", "summary": [{"text": "not exposed"}]},
            {"type": "function_call", "id": "item-1", "call_id": "call-1",
             "name": "get_team", "arguments": "{}"},
        ]),
        grok_response(),
    ])
    client = ProviderClient("grok", "SECRET", "owner-configured-model")

    async def scenario():
        first = await client.complete(MESSAGES, TOOLS, 800)
        assert first["tool_calls"] == [{"id": "call-1", "name": "get_team", "arguments": {}}]
        assert "not exposed" not in json.dumps(first)
        second = await client.complete([
            *MESSAGES, first["message"],
            {"role": "tool", "tool_call_id": "call-1", "content": '{"team_id":4}'},
        ], TOOLS, 800)
        assert second["text"] == "Ready"
        assert second["usage"]["input_tokens"] == 9

    asyncio.run(scenario())
    first = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://api.x.ai/v1/responses"
    assert requests[0].headers["authorization"] == "Bearer SECRET"
    assert first["model"] == "owner-configured-model"
    assert first["max_output_tokens"] == 800
    assert first["store"] is False
    assert first["tools"][0] == {"type": "function", **TOOLS[0]["function"]}
    assert "reasoning" not in first
    second = json.loads(requests[1].content)
    assert second["input"][-2] == {
        "type": "function_call", "call_id": "call-1", "name": "get_team", "arguments": "{}",
    }
    assert second["input"][-1]["call_id"] == "call-1"
    assert second["input"][-1]["type"] == "function_call_output"


@pytest.mark.parametrize("native_ids", [True, False])
def test_gemini_parallel_signature_roundtrip(transport, native_ids):
    parts = [
        {"text": "Reading both.", "thoughtSignature": "opaque-text-signature"},
        {"functionCall": {"name": "get_team", "args": {"part": 1}},
         "thoughtSignature": "opaque-function-signature"},
        {"functionCall": {"name": "get_team", "args": {"part": 2}}},
    ]
    if native_ids:
        parts[1]["functionCall"]["id"] = "native-a"
        parts[2]["functionCall"]["id"] = "native-b"
    requests = transport([gemini_response(parts), gemini_response()])
    client = ProviderClient("gemini", "SECRET", "models/owner-model")

    async def scenario():
        first = await client.complete(MESSAGES, TOOLS, 500)
        calls = first["tool_calls"]
        assert calls[0]["id"] != calls[1]["id"]
        assert first["message"]["_gemini_content"]["parts"] == parts
        assert first["text"] == "Reading both."
        result = await client.complete([
            *MESSAGES, deepcopy(first["message"]),
            {"role": "tool", "tool_call_id": calls[1]["id"], "content": '{"part":2}'},
            {"role": "tool", "tool_call_id": calls[0]["id"], "content": '{"part":1}'},
        ], TOOLS, 500)
        assert result["text"] == "Ready"

    asyncio.run(scenario())
    assert str(requests[0].url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/owner-model:generateContent"
    )
    assert requests[0].headers["x-goog-api-key"] == "SECRET"
    assert "SECRET" not in str(requests[0].url)
    first = json.loads(requests[0].content)
    assert first["systemInstruction"] == {"parts": [{"text": MESSAGES[0]["content"]}]}
    assert first["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == TOOLS[0]["function"]["parameters"]
    assert first["generationConfig"] == {"maxOutputTokens": 500}
    second = json.loads(requests[1].content)
    assert second["contents"][-2]["parts"] == parts
    results = second["contents"][-1]["parts"]
    assert [part["functionResponse"]["response"]["part"] for part in results] == [1, 2]
    if native_ids:
        assert [part["functionResponse"]["id"] for part in results] == ["native-a", "native-b"]
    else:
        assert all("id" not in part["functionResponse"] for part in results)


@pytest.mark.parametrize("provider", ["grok", "gemini"])
@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
def test_http_errors_are_safe(transport, provider, status):
    transport([httpx.Response(status, text="SECRET private response", headers={"Location": "https://evil.test"})])
    with pytest.raises(ProviderError, match=f"HTTP {status}") as caught:
        asyncio.run(ProviderClient(provider, "SECRET", "model").complete(MESSAGES, TOOLS, 50))
    assert "SECRET" not in str(caught.value)
    assert "private response" not in str(caught.value)


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("SECRET"), httpx.ConnectError("SECRET"),
    httpx.Response(200, text="SECRET"), httpx.Response(200, json=[]),
])
def test_network_and_json_errors_are_safe(transport, failure):
    transport([failure])
    with pytest.raises(ProviderError) as caught:
        asyncio.run(ProviderClient("grok", "SECRET", "model").complete(MESSAGES, [], 50))
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("arguments", ["not json", "[]", "null", '{"bad": NaN}', None])
def test_grok_rejects_malformed_arguments(transport, arguments):
    transport([grok_response([{"type": "function_call", "call_id": "a",
                               "name": "get_team", "arguments": arguments}])])
    with pytest.raises(ProviderError):
        asyncio.run(ProviderClient("grok", "key", "model").complete(MESSAGES, TOOLS, 50))


@pytest.mark.parametrize("provider", ["grok", "gemini"])
@pytest.mark.parametrize("problem", ["unknown", "duplicate", "invalid"])
def test_bad_tool_calls_fail_closed(transport, provider, problem):
    name = "undeclared" if problem == "unknown" else "get_team"
    if provider == "grok":
        item = {"type": "function_call", "call_id": "a", "name": name,
                "arguments": "[]" if problem == "invalid" else "{}"}
        response = grok_response([item, item] if problem == "duplicate" else [item])
    else:
        part = {"functionCall": {"name": name, "id": "a", "args": [] if problem == "invalid" else {}}}
        response = gemini_response([part, part] if problem == "duplicate" else [part])
    transport([response])
    with pytest.raises(ProviderError):
        asyncio.run(ProviderClient(provider, "key", "model").complete(MESSAGES, TOOLS, 50))


@pytest.mark.parametrize("response,provider", [
    (grok_response(status="incomplete"), "grok"),
    (grok_response(error={"message": "SECRET"}), "grok"),
    (gemini_response(finishReason="MAX_TOKENS"), "gemini"),
    (gemini_response(finishReason="SAFETY"), "gemini"),
    (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "gemini"),
    (httpx.Response(200, json={"candidates": [None]}), "gemini"),
])
def test_incomplete_or_blocked_responses(transport, response, provider):
    transport([response])
    with pytest.raises(ProviderError) as caught:
        asyncio.run(ProviderClient(provider, "key", "model").complete(MESSAGES, TOOLS, 50))
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("provider,response", [
    ("grok", httpx.Response(200, json={"id": "chosen-model", "object": "model", "aliases": []})),
    ("grok", httpx.Response(200, json={"id": "canonical-model", "object": "model",
                                      "aliases": ["chosen-model"]})),
    ("gemini", httpx.Response(200, json={"name": "models/chosen-model",
                                        "supportedGenerationMethods": ["generateContent"]})),
])
def test_connection_uses_authenticated_model_get_without_generation(transport, provider, response):
    requests = transport([response])
    result = asyncio.run(ProviderClient(provider, "key", "chosen-model").test_connection())
    assert "lookup verified without generation" in result
    assert "function calling have not been live-tested" in result
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].content == b""
    assert str(requests[0].url).endswith("/models/chosen-model")


@pytest.mark.parametrize("provider,metadata", [
    ("grok", {"id": "wrong-model", "object": "model"}),
    ("grok", {"id": "chosen-model", "object": "not-a-model"}),
    ("gemini", {"name": "models/wrong-model", "supportedGenerationMethods": ["generateContent"]}),
    ("gemini", {"name": "models/chosen-model", "supportedGenerationMethods": ["embedContent"]}),
    ("gemini", {}),
])
def test_connection_rejects_unverified_metadata(transport, provider, metadata):
    transport([httpx.Response(200, json=metadata)])
    with pytest.raises(ProviderError, match="metadata"):
        asyncio.run(ProviderClient(provider, "key", "chosen-model").test_connection())


@pytest.mark.parametrize("provider", ["grok", "gemini"])
def test_connection_unauthorized_is_safe(transport, provider):
    transport([httpx.Response(401, text="SECRET credentials")])
    with pytest.raises(ProviderError, match="HTTP 401") as caught:
        asyncio.run(ProviderClient(provider, "SECRET", "chosen-model").test_connection())
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("provider,model,key", [
    ("other", "model", "key"), ("grok", "", "key"), ("gemini", "../model", "key"),
    ("gemini", "https://evil.test/model", "key"), ("grok", "model", ""),
    ("grok", "model", "key\nInjected: header"),
])
def test_invalid_configuration(provider, model, key):
    with pytest.raises(ProviderError):
        ProviderClient(provider, key, model)


def test_invalid_transcript_and_definitions_do_not_request(transport):
    requests = transport([])
    client = ProviderClient("gemini", "key", "model")
    bad = [
        ([{"role": "tool", "tool_call_id": "missing", "content": "{}"}], TOOLS, 50),
        (MESSAGES, [{"type": "web_search"}], 50),
        (MESSAGES, TOOLS, 0),
        (MESSAGES, TOOLS, True),
        ([{"role": "user", "content": []}], TOOLS, 50),
        (MESSAGES, TOOLS * 2, 50),
        ([*MESSAGES, {"role": "assistant", "content": None, "tool_calls": [
            {"type": "function", "id": "a", "function": {"name": "get_team", "arguments": "{}"}},
        ]}], TOOLS, 50),
    ]
    for messages, tools, tokens in bad:
        with pytest.raises(ProviderError):
            asyncio.run(client.complete(messages, tools, tokens))
    assert not requests


def test_gemini_does_not_expose_thought_text(transport):
    transport([gemini_response([{"thought": True, "text": "private reasoning"}, {"text": "Ready"}])])
    result = asyncio.run(ProviderClient("gemini", "key", "model").complete(MESSAGES, [], 50))
    assert "private reasoning" not in json.dumps(result)


@pytest.mark.parametrize("provider", ["grok", "gemini"])
def test_actual_registry_pydantic_schemas_and_function_roundtrip(transport, provider):
    from app.models import ActionInput
    from app.tools import definitions

    tools = definitions()
    unchanged = deepcopy(tools)
    plan_schema = next(tool["function"]["parameters"] for tool in tools
                       if tool["function"]["name"] == "plan_action")
    assert "LineupMove" in plan_schema["$defs"]
    assert plan_schema["properties"]["moves"]["items"] == {"$ref": "#/$defs/LineupMove"}
    assert {"type": "null"} in plan_schema["properties"]["add_player_id"]["anyOf"]
    assert plan_schema["additionalProperties"] is False
    arguments = {"action": "set_lineup", "reason": "Owner-requested draft proposal only",
                 "moves": [{"player_id": -16001, "slot_id": 16}]}
    signed_part = {"functionCall": {"id": "native-plan", "name": "plan_action", "args": arguments},
                   "thoughtSignature": "opaque-signature-replayed-verbatim"}
    if provider == "grok":
        responses = [
            grok_response([{"type": "function_call", "call_id": "plan-1",
                            "name": "plan_action", "arguments": json.dumps(arguments)}]),
            grok_response(),
        ]
    else:
        responses = [gemini_response([signed_part]), gemini_response()]
    requests = transport(responses)
    client = ProviderClient(provider, "key", "owner-model")

    async def scenario():
        first = await client.complete(MESSAGES, tools, 1000)
        call = first["tool_calls"][0]
        assert call["name"] == "plan_action"
        assert ActionInput.model_validate(call["arguments"]).moves[0].player_id == -16001
        second = await client.complete([
            *MESSAGES, first["message"],
            {"role": "tool", "tool_call_id": call["id"], "name": call["name"],
             "content": '{"submitted":false,"status":"dry_run"}'},
        ], tools, 1000)
        assert second["text"] == "Ready"

    asyncio.run(scenario())
    for request in requests:
        payload = json.loads(request.content)
        if provider == "grok":
            assert payload["tools"] == [{"type": "function", **tool["function"]} for tool in tools]
        else:
            declarations = payload["tools"][0]["functionDeclarations"]
            assert len(declarations) == len(tools)
            for declared, original in zip(declarations, tools):
                assert declared["parametersJsonSchema"] == original["function"]["parameters"]
                assert "parameters" not in declared
            assert next(item for item in declarations if item["name"] == "plan_action")[
                "parametersJsonSchema"
            ] == plan_schema
    if provider == "gemini":
        second = json.loads(requests[1].content)
        assert second["contents"][-2] == {"role": "model", "parts": [signed_part]}
        assert second["contents"][-1]["parts"] == [{"functionResponse": {
            "id": "native-plan", "name": "plan_action",
            "response": {"submitted": False, "status": "dry_run"},
        }}]
    assert tools == unchanged


@pytest.mark.parametrize("provider", ["grok", "gemini"])
def test_nonfinite_usage_is_not_returned_or_persisted(transport, provider):
    from app.storage import encode

    response = grok_response() if provider == "grok" else gemini_response()
    body = json.loads(response.content)
    key = "usage" if provider == "grok" else "usageMetadata"
    body[key] = {
        "valid_count": 7, "valid_cost": 0.25, "nan_count": float("nan"),
        "infinite_count": float("inf"), "negative_infinite_count": float("-inf"),
        "boolean": True, "nested": {"private": "not accounting"}, "string": "not accounting",
    }
    transport([httpx.Response(200, content=json.dumps(body))])
    result = asyncio.run(ProviderClient(provider, "key", "model").complete(MESSAGES, [], 50))
    assert result["usage"] == {"valid_count": 7, "valid_cost": 0.25}
    assert json.loads(encode(result["usage"])) == result["usage"]
    json.dumps(result, allow_nan=False)
