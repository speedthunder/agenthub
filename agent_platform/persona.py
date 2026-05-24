"""Persona / fact extraction module.

從對話中抽取可長期記憶的使用者事實,寫入 user_facts。
萃取使用 agent 自己的 LLM 設定(共用 base_url / api_key),
以一次非串流 JSON-mode 呼叫完成。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from .database import get_conn
from .security_utils import validate_llm_base_url


# 允許的分類(限制 LLM 輸出)
ALLOWED_CATEGORIES = {
    "identity", "work", "family", "preference",
    "interest", "health", "event", "misc",
}

# 敏感詞:含這些字眼的 value 一律不存
BLOCKED_PATTERNS = [
    re.compile(r"password|密碼|passwd", re.I),
    re.compile(r"api[_\s-]?key|secret|token", re.I),
    re.compile(r"信用卡|credit\s*card|cvv|cvc", re.I),
    re.compile(r"身分證|身份證|ssn|social security", re.I),
]


# 將輸出格式改為 JSON Object {"facts": [...]} 以相容 OpenAI JSON Mode
EXTRACTION_PROMPT = """You are a memory extractor. From the latest user message and assistant reply, identify NEW long-term facts about the user that would be useful to remember in future conversations.

Output ONLY a JSON object with a single key "facts" containing an array of objects (no prose, no markdown fences). Each object must be:
{"category": "<one of: identity|work|family|preference|interest|health|event|misc>",
 "key": "<short snake_case>",
 "value": "<concise factual statement>",
 "confidence": <0.0-1.0>}

Rules:
- Output {"facts": []} if nothing memorable.
- Skip ephemeral chit-chat, opinions about the assistant, or facts the user is asking about (not asserting).
- Skip secrets: passwords, API keys, credit cards, ID numbers.
- Use the SAME language as the user when writing values.
- Keep values short (<80 chars).
- Use lowercase snake_case for key (e.g. pet_name, company, dietary_restriction).

Examples of good output:
{"facts": [{"category":"identity","key":"name","value":"小明","confidence":0.95}]}
{"facts": [{"category":"identity","key":"name","value":"李詩欽","confidence":0.95}]}
{"facts": [{"category":"family","key":"daughter_name","value":"小美 (5 歲)","confidence":0.9}]}

# Examples of user messages that should extract name:
# User: 我是李詩欽 → {"facts": [{"category":"identity","key":"name","value":"李詩欽","confidence":0.95}]}
# User: 我叫王大明 → {"facts": [{"category":"identity","key":"name","value":"王大明","confidence":0.95}]}
"""


async def extract_facts(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    user_message: str,
    assistant_message: str,
) -> list[dict]:
    """呼叫 LLM 萃取 facts。失敗回傳空 list，並記錄錯誤日誌。"""
    if not user_message:
        return []
    
    convo = (
        f"User said:\n{user_message[:2000]}\n\n"
        f"Assistant replied:\n{assistant_message[:2000]}"
    )
    
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": convo},
    ]
    
    try:
        validate_llm_base_url(base_url)
        if provider == "ollama":
            text = await _ollama_complete(base_url, model, messages)
        elif provider == "openai":
            text = await _openai_complete(base_url, api_key, model, messages, is_azure=False)
        elif provider == "azure":
            text = await _openai_complete(base_url, api_key, model, messages, is_azure=True)
        else:
            return []
    except Exception as e:
        # 加入 logging 避免 Silent Failure
        logging.error(f"[Fact Extraction] Failed to extract facts via {provider}: {e}")
        return []

    return _parse_and_validate(text)


def _parse_and_validate(text: str) -> list[dict]:
    if not text:
        return []
        
    # 容錯:剝掉可能的 ```json fence
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
        
    # 抓第一個 JSON 結構 (Object 或 Array)
    m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.S)
    if not m:
        return []
        
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
        
    # 支援 {"facts": [...]} 或是舊版的 [...] 直接回傳
    fact_list = []
    if isinstance(data, dict):
        fact_list = data.get("facts", [])
    elif isinstance(data, list):
        fact_list = data

    if not isinstance(fact_list, list):
        return []

    out = []
    for item in fact_list:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category", "")).strip().lower()
        key = str(item.get("key", "")).strip().lower()
        val = str(item.get("value", "")).strip()
        
        try:
            conf = float(item.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
            
        if cat not in ALLOWED_CATEGORIES or not key or not val:
            continue
        if len(key) > 60 or len(val) > 200:
            continue
        if any(p.search(val) or p.search(key) for p in BLOCKED_PATTERNS):
            continue
        if conf < 0.6:
            continue
            
        out.append({
            "category": cat,
            "key": re.sub(r"[^a-z0-9_]+", "_", key)[:60],
            "value": val,
            "confidence": max(0.0, min(1.0, conf)),
        })
        
    return out[:8]  # 一次最多 8 條,避免暴衝


async def _ollama_complete(base_url: str, model: str, messages: list[dict]) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model, 
        "messages": messages, 
        "stream": False,
        "format": "json", 
        "options": {"temperature": 0.1}
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("message", {}).get("content", "")


async def _openai_complete(base_url: str, api_key: str, model: str, messages: list[dict], is_azure: bool) -> str:
    url = base_url.rstrip("/")
    
    if is_azure:
        # Azure OpenAI: url 應該已經包含 deployment_id 與 api-version
        headers = {"api-key": api_key, "Content-Type": "application/json"}
    else:
        # Standard OpenAI
        url = f"{url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        
    payload = {
        "model": model, 
        "messages": messages, 
        "stream": False, 
        "temperature": 0.1,
        "response_format": {"type": "json_object"}  # 強制開啟 JSON Mode
    }
    
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]


def upsert_facts(
    user_id: int,
    facts: list[dict],
    *,
    agent_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
) -> int:
    """寫入 facts（同 category+key 直接覆蓋為較新版本）。回傳實際寫入筆數。

    agent_id 以 NULL 儲存代表跨 agent 通用記憶，避免觸發不存在 agent 的 FK 約束。
    SQLite 的 NULL != NULL 使 ON CONFLICT 無法可靠處理 NULL 欄位，
    因此改用 SELECT→UPDATE/INSERT 的方式確保正確 upsert。
    """
    if not facts:
        return 0
    n = 0
    with get_conn() as conn:
        for f in facts:
            existing = conn.execute(
                "SELECT id FROM user_facts WHERE user_id=? AND category=? AND key=?",
                (user_id, f["category"], f["key"]),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE user_facts
                       SET value=?, confidence=?, source_message_id=?,
                           updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (f["value"], f["confidence"], source_message_id, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO user_facts
                       (user_id, agent_id, category, key, value, confidence, source_message_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user_id, None, f["category"], f["key"],
                     f["value"], f["confidence"], source_message_id),
                )
            n += 1
    return n


def load_facts_for_prompt(user_id: int, agent_id: int, limit: int = 30) -> list[dict]:
    """撈出可注入 prompt 的所有 user facts（跨 agent 通用）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT category, key, value FROM user_facts
               WHERE user_id=?
               ORDER BY updated_at DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def build_facts_block(facts: list[dict]) -> str:
    if not facts:
        return ""
    lines = [f"- {f['category']}.{f['key']}: {f['value']}" for f in facts]
    return "\n\n[Known facts about the user — remembered from previous conversations]\n" + "\n".join(lines)