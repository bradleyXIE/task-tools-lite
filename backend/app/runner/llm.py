"""LLM adapter (httpx, no SDK). Two wire formats, selected by LLM_FORMAT:

  anthropic (default)  POST {base}/v1/messages           base default https://api.anthropic.com
  openai               POST {base}/chat/completions      base default https://api.openai.com/v1
                       (any OpenAI-compatible server: OpenAI, DeepSeek, Qwen, Ollama, vLLM, OpenRouter...)

The runner always speaks Anthropic-style blocks; the openai path translates both ways, so
`call()` always returns {content:[{type:text|tool_use,...}], stop_reason}.
"""
import json
import os
from typing import Any

import httpx


class LLMError(Exception):
    pass


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _cfg() -> tuple[str, str, str, str]:
    fmt = (os.environ.get("LLM_FORMAT") or "anthropic").strip().lower()
    default = "https://api.openai.com/v1" if fmt == "openai" else "https://api.anthropic.com"
    base = (os.environ.get("LLM_BASE_URL") or default).rstrip("/")
    return fmt, base, os.environ.get("LLM_API_KEY", ""), os.environ.get("LLM_MODEL", "claude-sonnet-5-5")


async def _post(url: str, headers: dict, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise LLMError(f"LLM request failed: {e}") from e
    if r.status_code >= 400:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except ValueError as e:
        raise LLMError(f"LLM returned non-JSON: {r.text[:200]}") from e


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "assistant":
            text = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in c if b.get("type") == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in c:
                if b.get("type") == "tool_result":
                    content = b.get("content")
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)})
                elif b.get("type") == "text":
                    out.append({"role": "user", "content": b.get("text", "")})
    return out


async def _call_openai(base: str, key: str, model: str, system: str, messages: list[dict],
                       tools: list[dict], max_tokens: int) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                            "messages": _to_openai_messages(system, messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
                         for t in tools]
    data = await _post(f"{base}/chat/completions", {"Authorization": f"Bearer {key}"}, body)
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected LLM response: {str(data)[:300]}") from e
    content: list[dict] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        content.append({"type": "tool_use", "id": tc.get("id") or f"call_{len(content)}",
                        "name": fn.get("name", ""), "input": args if isinstance(args, dict) else {}})
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
    u = data.get("usage") or {}
    return {"content": content, "stop_reason": stop,
            "usage": {"input_tokens": _int(u.get("prompt_tokens")), "output_tokens": _int(u.get("completion_tokens"))}}


async def call(system: str, messages: list[dict], tools: list[dict], max_tokens: int = 4096) -> dict[str, Any]:
    """`tools` = [{name, description, input_schema}]. Raises LLMError on failure."""
    fmt, base, key, model = _cfg()
    if fmt == "openai":
        return await _call_openai(base, key, model, system, messages, tools, max_tokens)
    if fmt != "anthropic":
        raise LLMError(f"unknown LLM_FORMAT '{fmt}' (use 'anthropic' or 'openai')")
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    data = await _post(f"{base}/v1/messages", headers, body)
    u = data.get("usage") or {}
    data["usage"] = {"input_tokens": _int(u.get("input_tokens")), "output_tokens": _int(u.get("output_tokens"))}
    return data


async def _ping() -> None:
    fmt, base, key, model = _cfg()
    print(f"format={fmt} base={base} model={model} key={'set' if key else 'MISSING'}")
    tool = {"name": "echo", "description": "Echo text back.",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
    r = await call("You are a test. Call the echo tool with text 'ok'.", [{"role": "user", "content": "go"}], [tool], 200)
    uses = [b for b in r["content"] if b["type"] == "tool_use"]
    print("OK: tool calling works" if uses else f"WARNING: reachable but no tool call returned: {r['content']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_ping())
