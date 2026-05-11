"""
Knowledge Base module — long-term memory tier 2-4.
Inspired by llm-knowledge-base (gatelynch/llm-knowledge-base).

Four-tier memory architecture:
  Tier 1  user_facts       simple key-value facts (existing, persona.py)
  Tier 2  kb_documents     raw materials uploaded by user
  Tier 3  kb_summaries     LLM-compiled summaries (wiki/summaries)
  Tier 4  kb_concepts      cross-document concept entries (wiki/concepts)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

# 英文停用詞（4 字以上才篩）
_STOP_EN = {
    'what', 'when', 'where', 'which', 'while', 'with', 'that', 'this',
    'from', 'have', 'will', 'your', 'they', 'been', 'more', 'very',
    'also', 'into', 'some', 'such', 'than', 'then', 'them', 'were',
    'each', 'much', 'does', 'like', 'just', 'over', 'know', 'take',
    'make', 'come', 'good', 'want', 'look', 'here', 'help', 'need',
    'please', 'about', 'would', 'there', 'could', 'other', 'these',
    'those', 'their', 'because', 'through', 'between',
}


def _extract_search_terms(text: str, max_terms: int = 6) -> list[str]:
    """從使用者訊息中提取關鍵詞，供 KB 檢索使用。

    中文：對連續漢字序列取 4→3→2 字的 n-gram，優先保留較長片語。
    英文：取 4+ 字母的單詞，過濾停用詞。
    """
    seen: set[str] = set()
    terms: list[str] = []

    # ── 中文 n-gram ──────────────────────────────────────────────────────
    for seq in re.findall(r'[一-鿿]+', text):
        for n in (4, 3, 2):
            for i in range(len(seq) - n + 1):
                t = seq[i:i + n]
                if t not in seen:
                    seen.add(t)
                    terms.append(t)
                    if len(terms) >= max_terms:
                        return terms

    # ── 英文單詞 ─────────────────────────────────────────────────────────
    for w in re.findall(r'[a-zA-Z]{4,}', text.lower()):
        if w not in _STOP_EN and w not in seen:
            seen.add(w)
            terms.append(w)
            if len(terms) >= max_terms:
                return terms

    return terms

import httpx

from .database import get_conn

# ── Prompts ───────────────────────────────────────────────────────────────────

_COMPILE_PROMPT = """You are a knowledge compiler. Read the text below and produce a structured summary.

Output ONLY a valid JSON object — no prose, no markdown fences:
{
  "title": "Short descriptive title (max 60 chars)",
  "core_conclusions": "Key takeaways as markdown bullet points (- prefix, max 500 chars)",
  "key_evidence": "Specific facts, data, or quotes supporting the conclusions (max 300 chars)",
  "questions": "Unverified claims or open questions; empty string if none (max 300 chars)",
  "terms": "Key terms and brief definitions; empty string if none (max 300 chars)",
  "tags": ["tag1", "tag2"],
  "concepts": [
    {"name": "ConceptName", "definition": "one-line definition (max 120 chars)", "my_practice": "", "external_views": ""}
  ]
}

Rules:
- tags: 2-5 lowercase single-word tags relevant to the content
- concepts: only clearly identifiable concepts; leave array empty [] if none stand out
- concepts.name: TitleCase, max 40 chars; only concepts that appear across multiple contexts
- Output valid JSON only
"""

_CONVO_COMPILE_PROMPT = """You are a knowledge archivist. Given this conversation, create a knowledge entry for future reference.

Output ONLY a valid JSON object:
{
  "title": "What this conversation was about (max 60 chars)",
  "core_conclusions": "Key learnings, decisions, or conclusions reached (max 500 chars)",
  "key_evidence": "Specific details, facts, code, or examples shared (max 300 chars)",
  "questions": "Unresolved issues or follow-up questions; empty if none (max 300 chars)",
  "tags": ["tag1", "tag2"],
  "concepts": [
    {"name": "ConceptName", "definition": "one-line definition (max 120 chars)"}
  ]
}

Rules:
- Focus on knowledge useful for future conversations, skip small talk
- concepts: only if a clear concept was explored; leave [] if not
- Output valid JSON only
"""


# ── Core compile functions ────────────────────────────────────────────────────

async def compile_document(
    *,
    document_id: int,
    user_id: int,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
) -> dict:
    """Compile a kb_document into a summary + extract concepts. Returns summary dict."""
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT * FROM kb_documents WHERE id=? AND user_id=?",
            (document_id, user_id),
        ).fetchone()
    if not doc:
        return {}
    doc = dict(doc)

    content = doc["content"][:4000]
    messages = [
        {"role": "system", "content": _COMPILE_PROMPT},
        {"role": "user", "content": content},
    ]

    try:
        raw = await _llm_complete(provider, model, base_url, api_key, messages)
        data = _parse_json(raw)
    except Exception as e:
        logging.error(f"[KB compile] doc_id={document_id} failed: {e}")
        return {}

    if not data:
        return {}

    title = str(data.get("title") or doc.get("title") or "")[:100]
    tags_json = json.dumps(data.get("tags", []), ensure_ascii=False)

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO kb_summaries
               (user_id, document_id, title, origin, core_conclusions,
                key_evidence, questions, terms, tags)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                user_id, document_id, title,
                doc.get("source_type", "note"),
                str(data.get("core_conclusions", ""))[:1000],
                str(data.get("key_evidence", ""))[:500],
                str(data.get("questions", ""))[:500],
                str(data.get("terms", ""))[:500],
                tags_json,
            ),
        )
        summary_id = cur.lastrowid
        conn.execute("UPDATE kb_documents SET compiled=1 WHERE id=?", (document_id,))

    _upsert_concepts(user_id, summary_id, data.get("concepts", []))
    return data


async def compile_conversation(
    *,
    conversation_id: int,
    user_id: int,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
) -> bool:
    """Compile the last 30 messages of a conversation into a KB summary (long-term memory)."""
    with get_conn() as conn:
        msgs = conn.execute(
            """SELECT role, content FROM messages
               WHERE conversation_id=? ORDER BY id ASC LIMIT 30""",
            (conversation_id,),
        ).fetchall()

    if not msgs:
        return False

    convo_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}" for m in msgs
    )[:4000]

    llm_messages = [
        {"role": "system", "content": _CONVO_COMPILE_PROMPT},
        {"role": "user", "content": convo_text},
    ]

    try:
        raw = await _llm_complete(provider, model, base_url, api_key, llm_messages)
        data = _parse_json(raw)
    except Exception as e:
        logging.error(f"[KB convo compile] conv_id={conversation_id} failed: {e}")
        return False

    if not data:
        return False

    title = str(data.get("title") or f"Conversation {conversation_id}")[:100]
    tags_json = json.dumps(data.get("tags", []), ensure_ascii=False)

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO kb_documents
               (user_id, title, content, source_type, source_ref, compiled)
               VALUES (?,?,?,?,?,1)""",
            (user_id, title, convo_text, "conversation", str(conversation_id)),
        )
        doc_id = cur.lastrowid
        cur2 = conn.execute(
            """INSERT INTO kb_summaries
               (user_id, document_id, title, origin, core_conclusions,
                key_evidence, questions, tags)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                user_id, doc_id, title, "conversation",
                str(data.get("core_conclusions", ""))[:1000],
                str(data.get("key_evidence", ""))[:500],
                str(data.get("questions", ""))[:500],
                tags_json,
            ),
        )
        summary_id = cur2.lastrowid

    _upsert_concepts(user_id, summary_id, data.get("concepts", []))
    return True


# ── Concept management ────────────────────────────────────────────────────────

def _upsert_concepts(user_id: int, summary_id: int, concepts: list) -> None:
    """Upsert concept entries. Only creates an independent entry when source_count >= 2."""
    for c in concepts:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()[:40]
        definition = str(c.get("definition", "")).strip()[:300]
        if not name or not definition:
            continue

        my_practice = str(c.get("my_practice", "")).strip()[:300]
        external_views = str(c.get("external_views", "")).strip()[:300]

        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, sources, source_count FROM kb_concepts WHERE user_id=? AND name=?",
                (user_id, name),
            ).fetchone()

            if existing:
                sources: list = json.loads(existing["sources"] or "[]")
                if summary_id not in sources:
                    sources.append(summary_id)
                conn.execute(
                    """UPDATE kb_concepts SET
                       definition=?, source_count=?, sources=?, updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (definition, len(sources), json.dumps(sources), existing["id"]),
                )
                if my_practice:
                    conn.execute(
                        "UPDATE kb_concepts SET my_practice=? WHERE id=?",
                        (my_practice, existing["id"]),
                    )
                if external_views:
                    conn.execute(
                        "UPDATE kb_concepts SET external_views=? WHERE id=?",
                        (external_views, existing["id"]),
                    )
            else:
                conn.execute(
                    """INSERT INTO kb_concepts
                       (user_id, name, definition, my_practice, external_views, sources, source_count)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user_id, name, definition, my_practice, external_views,
                     json.dumps([summary_id]), 1),
                )


# ── Prompt injection ──────────────────────────────────────────────────────────

def load_kb_for_prompt(user_id: int, limit: int = 5) -> str:
    """Return a compact [Long-term knowledge] block for system prompt injection."""
    with get_conn() as conn:
        summaries = conn.execute(
            """SELECT title, core_conclusions FROM kb_summaries
               WHERE user_id=? ORDER BY updated_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        concepts = conn.execute(
            """SELECT name, definition FROM kb_concepts
               WHERE user_id=? AND source_count >= 2
               ORDER BY source_count DESC LIMIT 5""",
            (user_id,),
        ).fetchall()

    lines: list[str] = []

    if summaries:
        lines.append("[Long-term knowledge — compiled from past interactions]")
        for s in summaries:
            title = s["title"] or "Untitled"
            snippet = (s["core_conclusions"] or "")[:150].replace("\n", " ")
            lines.append(f"• {title}: {snippet}")

    if concepts:
        lines.append("[Key concepts you know]")
        for c in concepts:
            lines.append(f"• {c['name']}: {c['definition']}")

    return ("\n\n" + "\n".join(lines)) if lines else ""


# ── Search ────────────────────────────────────────────────────────────────────

def search_kb(user_id: int, query: str, limit: int = 10) -> dict:
    """Keyword search across summaries and concepts."""
    q = f"%{query}%"
    with get_conn() as conn:
        summaries = conn.execute(
            """SELECT id, title, core_conclusions, tags, created_at FROM kb_summaries
               WHERE user_id=? AND (title LIKE ? OR core_conclusions LIKE ?)
               ORDER BY updated_at DESC LIMIT ?""",
            (user_id, q, q, limit),
        ).fetchall()
        concepts = conn.execute(
            """SELECT id, name, definition, source_count FROM kb_concepts
               WHERE user_id=? AND (name LIKE ? OR definition LIKE ?)
               ORDER BY source_count DESC LIMIT ?""",
            (user_id, q, q, limit),
        ).fetchall()

    return {
        "summaries": [dict(r) for r in summaries],
        "concepts": [dict(r) for r in concepts],
    }


def retrieve_kb_for_query(user_id: int, query: str, limit: int = 3) -> str:
    """根據使用者當前訊息動態檢索相關 KB 內容，回傳可注入 system prompt 的文字區塊。

    流程：
      1. 從使用者訊息提取關鍵詞（中文 n-gram + 英文單詞）
      2. 對 kb_summaries 和 kb_concepts 做多關鍵詞 OR LIKE 查詢
      3. 找到相關內容 → 回傳 [Relevant knowledge] 區塊
      4. 完全沒有相符內容 → 回傳空字串（不注入）
    """
    terms = _extract_search_terms(query)
    if not terms:
        return ""

    # 每個 term 對應 title / core_conclusions 兩個 LIKE 條件
    s_parts = " OR ".join("(title LIKE ? OR core_conclusions LIKE ?)" for _ in terms)
    s_params = [p for t in terms for p in (f"%{t}%", f"%{t}%")]

    c_parts = " OR ".join("(name LIKE ? OR definition LIKE ?)" for _ in terms)
    c_params = [p for t in terms for p in (f"%{t}%", f"%{t}%")]

    with get_conn() as conn:
        summaries = conn.execute(
            f"""SELECT title, core_conclusions FROM kb_summaries
                WHERE user_id=? AND ({s_parts})
                ORDER BY updated_at DESC LIMIT ?""",
            [user_id] + s_params + [limit],
        ).fetchall()

        concepts = conn.execute(
            f"""SELECT name, definition FROM kb_concepts
                WHERE user_id=? AND ({c_parts})
                ORDER BY source_count DESC LIMIT ?""",
            [user_id] + c_params + [limit],
        ).fetchall()

    lines: list[str] = []
    if summaries:
        lines.append(
            "[Relevant knowledge from your knowledge base]\n"
            "IMPORTANT: When your answer draws on any entry below, explicitly cite it "
            "by name — e.g. 「根據你知識庫中的《{title}》…」or「According to your KB entry '{title}'…」. "
            "If an entry is not relevant, ignore it silently."
        )
        for s in summaries:
            snippet = (s["core_conclusions"] or "")[:200].replace("\n", " ")
            lines.append(f"• 《{s['title'] or 'Untitled'}》: {snippet}")
    if concepts:
        lines.append("[Relevant concepts]")
        for c in concepts:
            lines.append(f"• 【{c['name']}】: {c['definition']}")

    return ("\n\n" + "\n".join(lines)) if lines else ""


# ── LLM helpers ──────────────────────────────────────────────────────────────

async def _llm_complete(
    provider: str, model: str, base_url: str, api_key: str, messages: list
) -> str:
    if provider == "ollama":
        return await _ollama(base_url, model, messages)
    elif provider in ("openai", "azure"):
        return await _openai(base_url, api_key, model, messages, is_azure=(provider == "azure"))
    return ""


async def _ollama(base_url: str, model: str, messages: list) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model, "messages": messages, "stream": False,
        "format": "json", "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=90) as c:
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
        "temperature": 0.1, "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}
