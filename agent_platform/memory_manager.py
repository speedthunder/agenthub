"""
Short-term session memory manager.

Tracks working context within the current conversation:
  current task/goal, stated constraints, in-progress state, session preferences.

This is separate from long-term user_facts (persona.py) and KB summaries (knowledge_base.py).
Session memory is scoped to one conversation_id and cleared when the conversation is deleted.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from .database import get_conn

# ── Prompt ────────────────────────────────────────────────────────────────────

_SESSION_MEMORY_PROMPT = """You are a working memory tracker for an AI assistant.
Given the latest exchange, identify items the AI should actively keep in mind for the REST of this conversation.

Focus ONLY on session-specific context:
- Current task or goal the user is pursuing right now
- Constraints stated in this conversation (e.g. "use Python 3.10", "don't use external libs")
- In-progress state (e.g. "we left off at step 3", "waiting for user to test X")
- Preferences expressed this session (different from long-term profile)

Do NOT store: general facts about the user (name, job) — those go to long-term memory.

Output ONLY a JSON object:
{"items": [{"key": "short_key", "value": "concise value (max 100 chars)"}]}

Rules:
- Max 5 items total
- key: lowercase_snake_case, max 30 chars
- Output {"items": []} if nothing meaningful to track beyond the conversation history itself
"""


# ── Extraction ────────────────────────────────────────────────────────────────

async def extract_session_memory(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    user_message: str,
    assistant_message: str,
) -> list[dict]:
    """Extract session working memory items from the latest exchange."""
    if not user_message:
        return []

    convo = f"User: {user_message[:1000]}\nAssistant: {assistant_message[:1000]}"
    messages = [
        {"role": "system", "content": _SESSION_MEMORY_PROMPT},
        {"role": "user", "content": convo},
    ]

    try:
        if provider == "ollama":
            raw = await _ollama(base_url, model, messages)
        elif provider in ("openai", "azure"):
            raw = await _openai(base_url, api_key, model, messages, is_azure=(provider == "azure"))
        else:
            return []
    except Exception as e:
        logging.error(f"[Session Memory] extract failed: {e}")
        return []

    return _parse_items(raw)


def _parse_items(text: str) -> list[dict]:
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []

    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        return []

    out: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        key = re.sub(r"[^a-z0-9_]+", "_", str(item.get("key", "")).strip().lower())[:30]
        value = str(item.get("value", "")).strip()[:100]
        if key and value:
            out.append({"key": key, "value": value})
    return out[:5]


# ── Storage ───────────────────────────────────────────────────────────────────

def upsert_session_memory(conversation_id: int, items: list[dict]) -> None:
    with get_conn() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO session_memory (conversation_id, key, value)
                   VALUES (?,?,?)
                   ON CONFLICT(conversation_id, key) DO UPDATE SET
                       value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                (conversation_id, item["key"], item["value"]),
            )


def load_session_memory(conversation_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM session_memory WHERE conversation_id=? ORDER BY updated_at DESC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def build_session_memory_block(items: list[dict]) -> str:
    """Format session memory for system prompt injection."""
    if not items:
        return ""
    lines = [f"- {item['key']}: {item['value']}" for item in items]
    return "\n\n[Working memory — active context from this conversation]\n" + "\n".join(lines)


# ── LLM helpers ──────────────────────────────────────────────────────────────

async def _ollama(base_url: str, model: str, messages: list) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model, "messages": messages, "stream": False,
        "format": "json", "options": {"temperature": 0.0},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


async def _openai(base_url: str, api_key: str, model: str, messages: list, is_azure: bool) -> str:
    if is_azure:
        url = base_url.rstrip("/")
        headers = {"api-key": api_key, "Content-Type": "application/json"}
    else:
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model, "messages": messages, "stream": False,
        "temperature": 0.0, "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
