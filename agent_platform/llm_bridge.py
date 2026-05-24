"""LLM 呼叫橋接層：支援 ollama / openai / azure"""
import httpx
import re
from typing import AsyncGenerator
from urllib.parse import urlparse

# H3 fix: SSRF 防護 — 禁止指向內網的 base_url
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|169\.254\.)",
    re.I,
)


def _validate_base_url(url: str) -> None:
    """驗證 LLM base_url 是否安全；不安全則拋出 ValueError。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if _BLOCKED_HOSTS.match(host):
        raise ValueError(f"Blocked internal host: {host}")


async def stream_chat(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    system_prompt: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:

    all_messages = [{"role": "system", "content": system_prompt}] + messages

    try:
        _validate_base_url(base_url)
    except ValueError as e:
        yield f"[Configuration error: {e}]"
        return

    if provider == "ollama":
        async for chunk in _ollama_stream(base_url, model, all_messages, temperature, max_tokens):
            yield chunk
    elif provider in ("openai", "azure"):
        async for chunk in _openai_stream(base_url, api_key, model, all_messages, temperature, max_tokens):
            yield chunk
    else:
        yield f"[Unsupported provider: {provider}]"


async def _ollama_stream(base_url, model, messages, temperature, max_tokens):
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            import json
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue


async def _openai_stream(base_url, api_key, model, messages, temperature, max_tokens):
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            import json
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    content = data["choices"][0]["delta"].get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
