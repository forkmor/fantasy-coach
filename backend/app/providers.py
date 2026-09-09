"""Small, non-executing HTTP adapters for the official provider REST APIs.

References: https://docs.x.ai/developers/rest-api-reference/inference/responses
https://docs.x.ai/developers/rest-api-reference/inference/models
https://docs.x.ai/developers/tools/function-calling
https://docs.x.ai/developers/model-capabilities/text/structured-outputs
https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta

httpx is used instead of two SDK dependencies. Models are always owner-configured.
The private Gemini message metadata is for in-memory replay, never audit logging.
"""

import asyncio
from copy import deepcopy
import json
import math
import re
from uuid import uuid4

import httpx


class ProviderError(Exception):
    """An actionable error safe to display without response bodies or credentials."""


def _object_arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ProviderError("Provider returned malformed function arguments") from None
    if not isinstance(value, dict):
        raise ProviderError("Function arguments must be a JSON object")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise ProviderError("Function arguments are not valid JSON") from None
    return value


def _calls(raw, allowed=None):
    if not isinstance(raw, list) or len(raw) > 128:
        raise ProviderError("Provider returned an invalid function-call list")
    result, seen = [], set()
    for call in raw:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ProviderError("Provider returned an unsupported tool call")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ProviderError("Provider returned a malformed function call")
        name, identifier = function.get("name"), call.get("id")
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name)
                or not isinstance(identifier, str) or not identifier or len(identifier) > 256
                or identifier in seen):
            raise ProviderError("Provider returned invalid or duplicate function identifiers")
        if allowed is not None and name not in allowed:
            raise ProviderError("Provider requested an undeclared function")
        seen.add(identifier)
        result.append({"id": identifier, "name": name,
                       "arguments": _object_arguments(function.get("arguments"))})
    return result


def _openai_calls(calls):
    return [{"id": call["id"], "type": "function", "function": {
        "name": call["name"], "arguments": json.dumps(call["arguments"], allow_nan=False),
    }} for call in calls]


def _usage(data):
    if not isinstance(data, dict):
        return {}
    # Keep bounded numeric accounting only, not arbitrary provider extensions.
    return {key: value for key, value in list(data.items())[:30]
            if isinstance(key, str) and len(key) < 80
            and (type(value) is int or type(value) is float and math.isfinite(value))}


class ProviderClient:
    def __init__(self, provider: str, api_key: str, model: str):
        if provider not in {"grok", "gemini"}:
            raise ProviderError("Choose the Grok or Gemini provider")
        if not isinstance(api_key, str) or not api_key.strip() or any(
                ord(char) < 33 or ord(char) > 126 for char in api_key):
            raise ProviderError("Configure a valid provider API key")
        if provider == "gemini" and isinstance(model, str):
            model = model.removeprefix("models/")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", model):
            raise ProviderError("Configure an explicit provider model ID, not a URL")
        self.provider, self._api_key, self.model = provider, api_key, model

    async def _request(self, payload=None, *, model_metadata=False):
        if self.provider == "grok":
            url = (f"https://api.x.ai/v1/models/{self.model}" if model_metadata
                   else "https://api.x.ai/v1/responses")
            headers = {"Authorization": f"Bearer {self._api_key}"}
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
            if not model_metadata:
                url += ":generateContent"
            headers = {"x-goog-api-key": self._api_key}
        request_options = {} if model_metadata else {"json": payload}
        try:
            async with asyncio.timeout(65):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream(
                        "GET" if model_metadata else "POST", url, headers=headers, **request_options,
                    ) as response:
                        if response.status_code != 200:
                            status = response.status_code
                            detail = {
                                400: "request rejected; check model and function support",
                                401: "authentication failed; check the API key",
                                403: "access denied; check model access and account permissions",
                                404: "configured model or endpoint not found",
                                429: "rate limit or quota exceeded",
                            }.get(status, "provider unavailable or request rejected")
                            raise ProviderError(f"{self.provider} HTTP {status}: {detail}")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 4_000_000:
                                raise ProviderError("Provider response exceeded the size limit")
            data = json.loads(body)
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderError(f"{self.provider} request timed out") from None
        except httpx.HTTPError:
            raise ProviderError(f"{self.provider} network request failed") from None
        except (ValueError, TypeError):
            raise ProviderError(f"{self.provider} returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ProviderError(f"{self.provider} returned an invalid response")
        return data

    @staticmethod
    def _validate(messages, tools, max_output_tokens):
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 131072:
            raise ProviderError("Output-token limit must be between 1 and 131072")
        if not isinstance(tools, list) or len(tools) > 128:
            raise ProviderError("Invalid tool definitions")
        names = set()
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                raise ProviderError("Only client-side function tools are supported")
            function = tool.get("function", {})
            if not isinstance(function, dict):
                raise ProviderError("Invalid function definition")
            name = function.get("name")
            parameters = function.get("parameters")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name)
                    or name in names or not isinstance(parameters, dict)
                    or parameters.get("type") != "object"
                    or not isinstance(function.get("description", ""), str)):
                raise ProviderError("Invalid or duplicate function definition")
            names.add(name)
        if not isinstance(messages, list) or not messages:
            raise ProviderError("A nonempty conversation is required")
        pending, seen = {}, set()
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "system", "developer", "user", "assistant", "tool",
            }:
                raise ProviderError("Invalid conversation role")
            role = message["role"]
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ProviderError("Only text conversation content is supported")
            if role == "tool":
                identifier = message.get("tool_call_id")
                if not isinstance(identifier, str) or identifier not in pending:
                    raise ProviderError("Tool result has no matching outstanding function call")
                if message.get("name", pending[identifier]) != pending[identifier]:
                    raise ProviderError("Tool result function name does not match its call")
                del pending[identifier]
            else:
                if pending:
                    raise ProviderError("Every function call requires a result before the next message")
                if role == "assistant":
                    for call in _calls(message.get("tool_calls", [])):
                        if call["id"] in seen:
                            raise ProviderError("Conversation contains duplicate function-call IDs")
                        seen.add(call["id"])
                        pending[call["id"]] = call["name"]
                elif message.get("tool_calls"):
                    raise ProviderError("Only assistant messages may request functions")
        if pending:
            raise ProviderError("Conversation has unanswered function calls")
        return names

    async def complete(self, messages: list[dict], tools: list[dict], max_output_tokens: int) -> dict:
        allowed = self._validate(messages, tools, max_output_tokens)
        if self.provider == "grok":
            payload = self._grok_input(messages, tools, max_output_tokens)
            return self._grok_output(await self._request(payload), allowed)
        payload = self._gemini_input(messages, tools, max_output_tokens)
        return self._gemini_output(await self._request(payload), allowed)

    async def test_connection(self) -> str:
        data = await self._request(model_metadata=True)
        if self.provider == "grok":
            aliases = data.get("aliases", [])
            valid = (data.get("object") == "model"
                     and (data.get("id") == self.model
                          or isinstance(aliases, list) and self.model in aliases))
        else:
            methods = data.get("supportedGenerationMethods")
            valid = (data.get("name") == f"models/{self.model}"
                     and isinstance(methods, list) and "generateContent" in methods)
        if not valid:
            raise ProviderError("Configured model metadata is missing, mismatched, or does not support content generation")
        return (f"{self.provider} authenticated model lookup verified without generation; "
                "generation and function calling have not been live-tested")

    def _grok_input(self, messages, tools, limit):
        items = []
        for message in messages:
            role, content = message["role"], message.get("content") or ""
            if role == "tool":
                items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                              "output": content})
            else:
                if content or role != "assistant":
                    items.append({"role": role, "content": content})
                for call in _calls(message.get("tool_calls", [])):
                    items.append({"type": "function_call", "call_id": call["id"],
                                  "name": call["name"], "arguments": json.dumps(call["arguments"])})
        return {"model": self.model, "input": items, "max_output_tokens": limit, "store": False,
                "tools": [{"type": "function", **tool["function"]} for tool in tools]}

    def _grok_output(self, data, allowed):
        if data.get("error") or data.get("status") != "completed":
            raise ProviderError("grok did not complete the response; no function calls will be executed")
        output = data.get("output")
        if not isinstance(output, list):
            raise ProviderError("grok returned an invalid output list")
        text, raw_calls = [], []
        for item in output:
            if not isinstance(item, dict):
                raise ProviderError("grok returned malformed output")
            if item.get("type") == "function_call":
                raw_calls.append({"id": item.get("call_id"), "type": "function", "function": {
                    "name": item.get("name"), "arguments": item.get("arguments"),
                }})
            elif item.get("type") == "message":
                parts = item.get("content")
                if not isinstance(parts, list):
                    raise ProviderError("grok returned malformed message content")
                for part in parts:
                    if not isinstance(part, dict):
                        raise ProviderError("grok returned malformed message content")
                    if part.get("type") == "refusal":
                        raise ProviderError("grok declined the request")
                    if part.get("type") == "output_text":
                        if not isinstance(part.get("text"), str):
                            raise ProviderError("grok returned invalid text")
                        text.append(part["text"])
            elif item.get("type") != "reasoning":
                raise ProviderError("grok returned an unsupported output type")
        calls = _calls(raw_calls, allowed)
        return self._result("".join(text), calls, data.get("usage"))

    def _gemini_input(self, messages, tools, limit):
        contents, system, pending, results = [], [], {}, {}
        for message in messages:
            role = message["role"]
            if role in {"system", "developer"}:
                system.append({"text": message.get("content") or ""})
            elif role == "tool":
                identifier = message["tool_call_id"]
                name, native_id = pending[identifier]
                content = message.get("content") or ""
                try:
                    result = json.loads(content)
                except ValueError:
                    result = {"output": content}
                if not isinstance(result, dict):
                    result = {"output": result}
                response = {"name": name, "response": result}
                if native_id is not None:
                    response["id"] = native_id
                results[identifier] = {"functionResponse": response}
                if len(results) == len(pending):
                    contents.append({"role": "user", "parts": [results[key] for key in pending]})
                    pending, results = {}, {}
            elif role == "assistant":
                calls = _calls(message.get("tool_calls", []))
                native = message.get("_gemini_content")
                if native is not None:
                    if not isinstance(native, dict) or not isinstance(native.get("parts"), list):
                        raise ProviderError("Invalid Gemini replay metadata")
                    native = deepcopy(native)
                    native_calls = [part["functionCall"] for part in native["parts"]
                                    if isinstance(part, dict) and "functionCall" in part]
                    if len(native_calls) != len(calls):
                        raise ProviderError("Gemini replay metadata does not match function calls")
                    for call, original in zip(calls, native_calls):
                        if (not isinstance(original, dict) or original.get("name") != call["name"]
                                or original.get("args", {}) != call["arguments"]):
                            raise ProviderError("Gemini replay metadata does not match function calls")
                        pending[call["id"]] = (call["name"], original.get("id"))
                    contents.append(native)
                else:
                    parts = []
                    if message.get("content"):
                        parts.append({"text": message["content"]})
                    for call in calls:
                        parts.append({"functionCall": {"name": call["name"], "args": call["arguments"]}})
                        pending[call["id"]] = (call["name"], None)
                    if parts:
                        contents.append({"role": "model", "parts": parts})
            else:
                contents.append({"role": "user", "parts": [{"text": message.get("content") or ""}]})
        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": limit}}
        if system:
            payload["systemInstruction"] = {"parts": system}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["function"]["name"],
                 "description": tool["function"].get("description", ""),
                 "parametersJsonSchema": tool["function"]["parameters"]} for tool in tools
            ]}]
        return payload

    def _gemini_output(self, data, allowed):
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ProviderError("gemini returned no single usable candidate; request may have been blocked")
        candidate = candidates[0]
        if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
            raise ProviderError("gemini response was blocked or incomplete; no function calls will be executed")
        content = candidate.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise ProviderError("gemini returned malformed content")
        text, raw_calls, replay_parts = [], [], []
        for part in content["parts"]:
            if not isinstance(part, dict):
                raise ProviderError("gemini returned a malformed content part")
            if part.get("thought"):
                continue  # Thought text is neither exposed nor requested as conversation content.
            if "functionCall" in part:
                function = part["functionCall"]
                if not isinstance(function, dict):
                    raise ProviderError("gemini returned a malformed function call")
                raw_calls.append({"id": function.get("id", f"gemini_{uuid4().hex}"),
                                  "type": "function", "function": {
                                      "name": function.get("name"),
                                      "arguments": function.get("args", {}),
                                  }})
            elif "text" in part:
                if not isinstance(part["text"], str):
                    raise ProviderError("gemini returned invalid text")
                text.append(part["text"])
            else:
                raise ProviderError("gemini returned an unsupported content part")
            # Keep signed parts intact and in order, including text-part signatures.
            replay_parts.append(deepcopy(part))
        result = self._result("".join(text), _calls(raw_calls, allowed), data.get("usageMetadata"))
        result["message"]["_gemini_content"] = {"role": "model", "parts": replay_parts}
        return result

    @staticmethod
    def _result(text, calls, usage):
        if not text and not calls:
            raise ProviderError("Provider returned no text or function calls")
        message = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = _openai_calls(calls)
        return {"message": message, "text": text, "tool_calls": calls, "usage": _usage(usage)}
