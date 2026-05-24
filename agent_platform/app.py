"""
Agent Platform – 主應用程式
FastAPI + SQLite + JWT + LLM streaming
"""
import json as _json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status, Request, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError

from .database import init_db, get_conn
from .auth import (
    hash_password, verify_password,
    create_access_token, decode_token, generate_slug,
)
from .schemas import (
    RegisterRequest, LoginRequest, TokenResponse, GoogleAuthRequest, UserOut,
    AgentCreate, AgentUpdate, AgentOut,
    ChatRequest, ConversationOut, MessageOut,
    ProfileOut, ProfileUpdate, FactOut,
    KBDocumentCreate, KBDocumentOut, KBSummaryOut, KBConceptOut, KBCompileRequest,
    SessionMemoryOut, SessionMemoryItem,
)
from .llm_bridge import stream_chat
from .persona import (
    extract_facts, upsert_facts, load_facts_for_prompt, build_facts_block,
)
from .knowledge_base import (
    compile_document, compile_conversation,
    load_kb_for_prompt, search_kb, retrieve_kb_for_query,
)
from .memory_manager import (
    extract_session_memory, upsert_session_memory,
    load_session_memory, build_session_memory_block,
)
from .config import CONTEXT_WINDOW_DIALOGS, PERSONA_UPDATE_INTERVAL, SESSION_MEMORY_INTERVAL, KB_COMPILE_INTERVAL
from .security_utils import encrypt_llm_api_key, decrypt_llm_api_key

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "agenthub_session")
AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "false").lower() == "true"
_cookie_same_site = os.environ.get("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
AUTH_COOKIE_SAMESITE = _cookie_same_site if _cookie_same_site in {"lax", "strict", "none"} else "lax"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 7
LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300"))
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = int(os.environ.get("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "8"))
LOGIN_RATE_LIMIT_BLOCK_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_BLOCK_SECONDS", "600"))
_login_attempts: dict[str, dict[str, float | int]] = {}
_login_attempts_lock = threading.Lock()

# ── Lifespan ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="AgentHub", version="1.0.0", lifespan=lifespan)

# 使用同源靜態頁，跨網域如需 cookie 請改用明確 allow_origins 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (UI) ─────────────────────────────────────
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ── Auth helpers ──────────────────────────────────────────
# auto_error=False: 允許同時支援 Cookie 與 Header，並讓 optional auth 可回傳 None。
bearer_scheme = HTTPBearer(auto_error=False)


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=AUTH_COOKIE_MAX_AGE,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")


def _auth_token_from_request(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = None,
) -> str | None:
    if creds and creds.credentials:
        return creds.credentials
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        return token
    return None


def _decode_and_load_user(token: str) -> dict:
    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = int(sub)
        token_version = int(payload.get("tv", 0))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    user = dict(row)
    if int(user.get("token_version", 0)) != token_version:
        raise HTTPException(status_code=401, detail="Token revoked")
    return user


def _row_token_version(row) -> int:
    try:
        if hasattr(row, "keys") and "token_version" in row.keys():
            return int(row["token_version"] or 0)
    except (TypeError, ValueError, KeyError):
        pass
    return 0


def _client_ip(request: Request) -> str:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def _create_user_token(user_id: int, token_version: int = 0) -> str:
    return create_access_token({"sub": str(user_id), "tv": int(token_version)})


def _auth_rate_keys(request: Request, identifier: str) -> list[str]:
    ip = _client_ip(request)
    ident = (identifier or "").strip().lower() or "_"
    return [f"ip:{ip}", f"acct:{ip}:{ident}"]


def _enforce_login_rate_limit(request: Request, identifier: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        for key in _auth_rate_keys(request, identifier):
            rec = _login_attempts.get(key)
            if not rec:
                continue
            blocked_until = float(rec.get("blocked_until", 0))
            if blocked_until > now:
                retry_after = max(1, int(blocked_until - now))
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            window_start = float(rec.get("window_start", now))
            if now - window_start > LOGIN_RATE_LIMIT_WINDOW_SECONDS:
                _login_attempts.pop(key, None)


def _record_login_failure(request: Request, identifier: str) -> None:
    now = time.time()
    with _login_attempts_lock:
        for key in _auth_rate_keys(request, identifier):
            rec = _login_attempts.get(key)
            if not rec or now - float(rec.get("window_start", now)) > LOGIN_RATE_LIMIT_WINDOW_SECONDS:
                _login_attempts[key] = {"count": 1, "window_start": now, "blocked_until": 0}
                continue
            rec["count"] = int(rec.get("count", 0)) + 1
            if int(rec["count"]) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
                rec["blocked_until"] = now + LOGIN_RATE_LIMIT_BLOCK_SECONDS
            _login_attempts[key] = rec


def _record_login_success(request: Request, identifier: str) -> None:
    with _login_attempts_lock:
        for key in _auth_rate_keys(request, identifier):
            _login_attempts.pop(key, None)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    token = _auth_token_from_request(request, creds)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _decode_and_load_user(token)


def get_optional_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_anon_session: str | None = Header(default=None, alias="X-Anon-Session"),
) -> dict | None:
    """Same as get_current_user but returns None instead of raising when no/invalid token."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif not x_anon_session:
        # 匿名會話優先走 X-Anon-Session 隔離，不自動套用瀏覽器 cookie 身分。
        token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    try:
        return _decode_and_load_user(token)
    except HTTPException:
        return None


def _resolve_identity(user: dict | None, anon_session: str | None) -> tuple[int | None, str | None]:
    """回傳 (user_id, anon_session_id)。登入優先;否則用前端送來的匿名 session id。"""
    if user:
        return user["id"], None
    if anon_session and 8 <= len(anon_session) <= 64:
        return None, anon_session
    return None, None


# ── Profile helpers (Phase 2) ─────────────────────────────
def _load_profile(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def _profile_to_out(p: dict | None) -> ProfileOut:
    if not p:
        return ProfileOut()
    return ProfileOut(
        display_name=p.get("display_name") or "",
        language=p.get("language") or "",
        timezone=p.get("timezone") or "",
        reply_style=p.get("reply_style") or "",
        tone=p.get("tone") or "",
        use_emoji=p.get("use_emoji") if p.get("use_emoji") is not None else -1,
        interests=_parse_json_field(p.get("interests"), []),
        custom_instructions=p.get("custom_instructions") or "",
        share_with_agents=bool(p.get("share_with_agents", 1)),
    )


def _build_persona_block(profile: dict | None) -> str:
    """把 profile 轉成可注入 system prompt 的 [About the user] 區塊;若不該注入回傳空字串。"""
    if not profile or not profile.get("share_with_agents", 1):
        return ""
    lines = []
    if profile.get("display_name"):
        lines.append(f"- Preferred name: {profile['display_name']}")
    if profile.get("language"):
        lines.append(f"- Preferred reply language: {profile['language']}")
    if profile.get("timezone"):
        lines.append(f"- Timezone: {profile['timezone']}")
    style = profile.get("reply_style")
    if style:
        lines.append(f"- Reply style preference: {style}")
    tone = profile.get("tone")
    if tone:
        lines.append(f"- Preferred tone: {tone}")
    ue = profile.get("use_emoji", -1)
    if ue == 0:
        lines.append("- Do NOT use emoji in replies.")
    elif ue == 1:
        lines.append("- Feel free to use emoji in replies.")
    interests = _parse_json_field(profile.get("interests"), [])
    if interests:
        lines.append(f"- Interests: {', '.join(str(x) for x in interests)}")
    ci = (profile.get("custom_instructions") or "").strip()
    if ci:
        lines.append(f"- Custom instructions from the user: {ci}")
    if not lines:
        return ""
    return "\n\n[About the user]\n" + "\n".join(lines)


def _parse_json_field(val, default):
    if isinstance(val, (list, dict)):
        return val
    try:
        return _json.loads(val) if val else default
    except Exception:
        return default


def _agent_out(row: dict, base_url_root: str) -> AgentOut:
    return AgentOut(
        **{k: row[k] for k in (
            "id", "slug", "name", "description", "avatar",
            "system_prompt", "llm_provider", "llm_model", "llm_base_url",
            "temperature", "max_tokens", "owner_id",
        )},
        is_public=bool(row["is_public"]),
        share_url=f"{base_url_root}/share/{row['slug']}",
        welcome_message=row.get("welcome_message") or "",
        suggested_prompts=_parse_json_field(row.get("suggested_prompts"), []),
        cover_image_url=row.get("cover_image_url") or "",
        theme_color=row.get("theme_color") or "#6c63ff",
        skills=_parse_json_field(row.get("skills"), []),
    )


# ── Auth routes ───────────────────────────────────────────
@app.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, response: Response):
    hashed = hash_password(req.password)
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, hashed_pw) VALUES (?,?,?)",
                (req.username, req.email, hashed),
            )
            user_id = cur.lastrowid
    except Exception:
        raise HTTPException(status_code=409, detail="Username or email already exists")
    token = _create_user_token(user_id, token_version=0)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, response: Response):
    _enforce_login_rate_limit(request, req.email)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (req.email,)).fetchone()
    # M1 fix: Google-only 帳號的 hashed_pw 是 "GOOGLE_OAUTH"，不能用 bcrypt 驗證
    if not row or row["hashed_pw"] == "GOOGLE_OAUTH":
        _record_login_failure(request, req.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, row["hashed_pw"]):
        _record_login_failure(request, req.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _create_user_token(row["id"], token_version=_row_token_version(row))
    _record_login_success(request, req.email)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@app.get("/auth/me", response_model=UserOut)
def me(user=Depends(get_current_user)):
    return UserOut(**{k: user[k] for k in ("id", "username", "email")})


@app.get("/auth/config")
def auth_config():
    return {"google_client_id": GOOGLE_CLIENT_ID}


@app.post("/auth/google", response_model=TokenResponse)
async def google_auth(req: GoogleAuthRequest, request: Request, response: Response):
    import httpx
    _enforce_login_rate_limit(request, "google")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": req.credential},
            timeout=10.0,
        )
    if r.status_code != 200:
        _record_login_failure(request, "google")
        raise HTTPException(status_code=401, detail="Google token 驗證失敗")
    info = r.json()
    if info.get("aud") != GOOGLE_CLIENT_ID:
        _record_login_failure(request, "google")
        raise HTTPException(status_code=401, detail="Token audience 不符")
    if info.get("email_verified") not in ("true", True):
        _record_login_failure(request, "google")
        raise HTTPException(status_code=401, detail="Email 尚未驗證")

    google_id = info["sub"]
    email = info["email"]
    name = info.get("name", email.split("@")[0])

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                conn.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, row["id"]))
            else:
                username = name.replace(" ", "_")[:40]
                base = username
                i = 1
                while conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                    username = f"{base}_{i}"
                    i += 1
                cur = conn.execute(
                    "INSERT INTO users (username, email, hashed_pw, google_id) VALUES (?,?,?,?)",
                    (username, email, "GOOGLE_OAUTH", google_id),
                )
                row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        user_id = row["id"]

    token = _create_user_token(user_id, token_version=_row_token_version(row))
    _record_login_success(request, "google")
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@app.post("/auth/logout", status_code=204)
def logout(response: Response, user=Depends(get_current_user)):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE id = ?",
            (user["id"],),
        )
    _clear_auth_cookie(response)


# ── Agent CRUD ────────────────────────────────────────────
@app.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(req: AgentCreate, request_obj=None, user=Depends(get_current_user)):
    from fastapi import Request
    slug = generate_slug()
    try:
        encrypted_llm_api_key = encrypt_llm_api_key(req.llm_api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    with get_conn() as conn:
        # ensure slug uniqueness
        while conn.execute("SELECT 1 FROM agents WHERE slug=?", (slug,)).fetchone():
            slug = generate_slug()
        cur = conn.execute(
            """INSERT INTO agents
               (owner_id, slug, name, description, avatar, system_prompt,
                llm_provider, llm_model, llm_base_url, llm_api_key,
                temperature, max_tokens, is_public,
                welcome_message, suggested_prompts, cover_image_url, theme_color, skills)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user["id"], slug, req.name, req.description, req.avatar,
                req.system_prompt, req.llm_provider, req.llm_model,
                req.llm_base_url, encrypted_llm_api_key,
                req.temperature, req.max_tokens, int(req.is_public),
                req.welcome_message or "",
                _json.dumps(req.suggested_prompts or [], ensure_ascii=False),
                req.cover_image_url or "",
                req.theme_color or "#6c63ff",
                _json.dumps(req.skills or [], ensure_ascii=False),
            ),
        )
        row = conn.execute("SELECT * FROM agents WHERE id=?", (cur.lastrowid,)).fetchone()
    return _agent_out(dict(row), "")


@app.get("/agents", response_model=list[AgentOut])
def list_my_agents(user=Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agents WHERE owner_id=? ORDER BY created_at DESC", (user["id"],)
        ).fetchall()
    return [_agent_out(dict(r), "") for r in rows]


@app.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id=? AND owner_id=?", (agent_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_out(dict(row), "")


@app.put("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, req: AgentUpdate, user=Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id=? AND owner_id=?", (agent_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        updates = req.model_dump(exclude_none=True)
        if "llm_api_key" in updates:
            # API key 欄位留空視為「不更新既有 key」，避免覆蓋成空值。
            if not (updates.get("llm_api_key") or "").strip():
                updates.pop("llm_api_key", None)
            else:
                try:
                    updates["llm_api_key"] = encrypt_llm_api_key(updates["llm_api_key"])
                except RuntimeError as e:
                    raise HTTPException(status_code=500, detail=str(e))
        if not updates:
            return _agent_out(dict(row), "")
        # serialize list/dict fields to JSON string for SQLite
        for json_field in ("suggested_prompts", "skills"):
            if json_field in updates:
                updates[json_field] = _json.dumps(updates[json_field], ensure_ascii=False)
        set_clause = ", ".join(f"{k}=?" for k in updates)
        set_clause += ", updated_at=CURRENT_TIMESTAMP"
        values = list(updates.values()) + [agent_id]
        conn.execute(f"UPDATE agents SET {set_clause} WHERE id=?", values)
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return _agent_out(dict(row), "")


@app.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM agents WHERE id=? AND owner_id=?", (agent_id, user["id"])
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Agent not found")


# ── Public share & chat ───────────────────────────────────
@app.get("/share/{slug}", response_class=HTMLResponse)
def share_page(slug: str):
    """回傳分享頁 HTML，嵌入 slug 後由前端 JS 處理。"""
    import re as _re
    # H1 fix: 只允許 generate_slug() 產生的合法字元，防止反射型 XSS
    if not _re.fullmatch(r"[a-z0-9\-]{1,80}", slug):
        raise HTTPException(status_code=404, detail="Agent not found")
    html_path = os.path.join(STATIC_DIR, "chat.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=500, detail="UI not built")
    with open(html_path, encoding="utf-8") as f:
        content = f.read()
    content = content.replace("__AGENT_SLUG__", slug)
    return HTMLResponse(content=content)


@app.get("/api/agents/public/{slug}")
def get_public_agent_info(slug: str):
    """讓前端取得 agent 公開資訊（不含 api_key）。"""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, slug, name, description, avatar,
                      welcome_message, suggested_prompts, cover_image_url, theme_color, skills
               FROM agents WHERE slug=? AND is_public=1""",
            (slug,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found or not public")
    data = dict(row)
    data["suggested_prompts"] = _parse_json_field(data.get("suggested_prompts"), [])
    data["skills"] = _parse_json_field(data.get("skills"), [])
    return data


@app.post("/api/chat/{slug}")
async def chat(
    slug: str,
    req: ChatRequest,
    user: dict | None = Depends(get_optional_user),
    x_anon_session: str | None = Header(default=None),
):
    """Streaming chat endpoint(公開,任何人可呼叫)。
    若帶 JWT 或 X-Anon-Session header,會把對話寫入 conversations / messages。
    新建的 conversation_id 會透過 response header `X-Conversation-Id` 回傳。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE slug=? AND is_public=1", (slug,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found or not public")
    agent = dict(row)
    try:
        runtime_api_key = decrypt_llm_api_key(agent.get("llm_api_key"))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 只取最新 CONTEXT_WINDOW_DIALOGS 則
    messages = [{"role": m.role, "content": m.content} for m in req.messages][-CONTEXT_WINDOW_DIALOGS:]

    # ── 持久化:解析身份並建立/取得 conversation ───────────────
    user_id, anon_session_id = _resolve_identity(user, x_anon_session)
    persist = user_id is not None or anon_session_id is not None
    conversation_id = req.conversation_id
    last_user_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)

    # ── Persona 注入(僅登入用戶,且未關閉分享開關) ──────────
    system_prompt = agent["system_prompt"]
    profile_for_chat = None
    facts_sharing_ok = True
    if user_id is not None:
        profile_for_chat = _load_profile(user_id)
        if profile_for_chat is not None and not profile_for_chat.get("share_with_agents", 1):
            facts_sharing_ok = False
        persona_block = _build_persona_block(profile_for_chat)
        if persona_block:
            system_prompt = system_prompt + persona_block
        # Phase 3: 長期 facts
        if facts_sharing_ok:
            facts = load_facts_for_prompt(user_id, agent["id"])
            facts_block = build_facts_block(facts)
            if facts_block:
                system_prompt = system_prompt + facts_block
            # Long-term KB：依當前問題動態檢索相關內容
            if last_user_msg:
                kb_block = retrieve_kb_for_query(user_id, last_user_msg["content"])
            else:
                kb_block = load_kb_for_prompt(user_id)
            if kb_block:
                system_prompt = system_prompt + kb_block

    if persist:
        with get_conn() as conn:
            if conversation_id:
                # 驗證該 conversation 屬於本人且屬於此 agent
                conv = conn.execute(
                    "SELECT * FROM conversations WHERE id=? AND agent_id=?",
                    (conversation_id, agent["id"]),
                ).fetchone()
                if not conv:
                    raise HTTPException(status_code=404, detail="Conversation not found")
                # ownership check — 確保記憶完全隔離在自己的帳號下
                if user_id is not None and conv["user_id"] != user_id:
                    raise HTTPException(status_code=403, detail="Not your conversation")
                if user_id is None and conv["anon_session_id"] != anon_session_id:
                    raise HTTPException(status_code=403, detail="Not your conversation")
            else:
                title = ""
                if last_user_msg:
                    title = last_user_msg["content"].strip().splitlines()[0][:40]
                cur = conn.execute(
                    """INSERT INTO conversations (agent_id, user_id, anon_session_id, title)
                       VALUES (?,?,?,?)""",
                    (agent["id"], user_id, anon_session_id, title),
                )
                conversation_id = cur.lastrowid

            # 寫入最新一則 user 訊息(僅最後一則,避免重複寫入歷史)
            if last_user_msg:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                    (conversation_id, "user", last_user_msg["content"]),
                )
                conn.execute(
                    "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (conversation_id,),
                )

    # ── 短期記憶注入 ──────────────────────────────────────────
    # 必須在 ownership check 之後、確認 conversation_id 屬於本人才載入
    # 僅登入用戶（user_id is not None）享有記憶功能，匿名不注入
    if user_id is not None and conversation_id and facts_sharing_ok:
        session_items = load_session_memory(conversation_id)
        session_block = build_session_memory_block(session_items)
        if session_block:
            system_prompt = system_prompt + session_block

    async def event_stream():
        assistant_buf: list[str] = []
        try:
            async for chunk in stream_chat(
                provider=agent["llm_provider"],
                model=agent["llm_model"],
                base_url=agent["llm_base_url"],
                api_key=runtime_api_key,
                system_prompt=system_prompt,
                messages=messages,
                temperature=agent["temperature"],
                max_tokens=agent["max_tokens"],
            ):
                assistant_buf.append(chunk)
                yield f"data: {chunk}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"
        yield "data: [DONE]\n\n"

        # ── 串流結束後：持久化 + 背景記憶萃取 ────────────────────────────────
        if not (persist and conversation_id and assistant_buf):
            return
        full = "".join(assistant_buf)
        assistant_msg_id = None
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                    (conversation_id, "assistant", full),
                )
                assistant_msg_id = cur.lastrowid
                conn.execute(
                    "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (conversation_id,),
                )
        except Exception as e:
            print(f"[DEBUG] assistant message persist error: {e}")
            return

        if not (user_id is not None and facts_sharing_ok and last_user_msg):
            return

        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                (conversation_id,)
            ).fetchone()[0]

        llm_cfg = dict(
            provider=agent["llm_provider"],
            model=agent["llm_model"],
            base_url=agent["llm_base_url"],
            api_key=runtime_api_key,
        )

        # ── 短期記憶：每 SESSION_MEMORY_INTERVAL 則更新 working memory ──────
        if count % SESSION_MEMORY_INTERVAL == 0:
            try:
                session_items = await extract_session_memory(
                    **llm_cfg,
                    user_message=last_user_msg["content"],
                    assistant_message=full,
                )
                if session_items and conversation_id:
                    upsert_session_memory(conversation_id, session_items)
            except Exception as e:
                print(f"[DEBUG] session memory error: {e}")

        # ── 長期 facts：每 PERSONA_UPDATE_INTERVAL 則萃取一次 ────────────────
        if count % PERSONA_UPDATE_INTERVAL == 0:
            try:
                new_facts = await extract_facts(
                    **llm_cfg,
                    user_message=last_user_msg["content"],
                    assistant_message=full,
                )
                upsert_facts(
                    user_id, new_facts,
                    agent_id=None,
                    source_message_id=assistant_msg_id,
                )
            except Exception as e:
                print(f"[DEBUG] extract_facts error: {e}")

        # ── 長期 KB：每 KB_COMPILE_INTERVAL 則把對話編譯進知識庫 ─────────────
        if count % KB_COMPILE_INTERVAL == 0 and conversation_id:
            try:
                await compile_conversation(
                    **llm_cfg,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
            except Exception as e:
                print(f"[DEBUG] kb compile_conversation error: {e}")

    headers = {}
    if conversation_id:
        headers["X-Conversation-Id"] = str(conversation_id)
        headers["Access-Control-Expose-Headers"] = "X-Conversation-Id"
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


# ── Conversation management ───────────────────────────────
def _resolve_identity_required(user: dict | None, anon_session: str | None):
    user_id, anon_id = _resolve_identity(user, anon_session)
    if user_id is None and anon_id is None:
        raise HTTPException(status_code=401, detail="Login or X-Anon-Session header required")
    return user_id, anon_id


def _conv_filter_clause(user_id: int | None, anon_id: str | None) -> tuple[str, list]:
    if user_id is not None:
        return "user_id=?", [user_id]
    return "anon_session_id=?", [anon_id]


@app.get("/api/conversations", response_model=list[ConversationOut])
def list_conversations(
    agent_slug: str | None = None,
    user: dict | None = Depends(get_optional_user),
    x_anon_session: str | None = Header(default=None),
):
    user_id, anon_id = _resolve_identity_required(user, x_anon_session)
    where, params = _conv_filter_clause(user_id, anon_id)
    sql = f"""SELECT c.id, c.agent_id, c.title, c.created_at, c.updated_at
              FROM conversations c
              WHERE c.{where}"""
    if agent_slug:
        sql += " AND c.agent_id=(SELECT id FROM agents WHERE slug=?)"
        params = params + [agent_slug]
    sql += " ORDER BY c.updated_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [ConversationOut(**dict(r)) for r in rows]


@app.get("/api/conversations/{conv_id}/messages", response_model=list[MessageOut])
def get_conversation_messages(
    conv_id: int,
    user: dict | None = Depends(get_optional_user),
    x_anon_session: str | None = Header(default=None),
):
    user_id, anon_id = _resolve_identity_required(user, x_anon_session)
    where, params = _conv_filter_clause(user_id, anon_id)
    with get_conn() as conn:
        conv = conn.execute(
            f"SELECT id FROM conversations WHERE id=? AND {where}",
            [conv_id] + params,
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
    return [MessageOut(**dict(r)) for r in rows]


@app.delete("/api/conversations/{conv_id}", status_code=204)
def delete_conversation(
    conv_id: int,
    user: dict | None = Depends(get_optional_user),
    x_anon_session: str | None = Header(default=None),
):
    user_id, anon_id = _resolve_identity_required(user, x_anon_session)
    where, params = _conv_filter_clause(user_id, anon_id)
    with get_conn() as conn:
        result = conn.execute(
            f"DELETE FROM conversations WHERE id=? AND {where}",
            [conv_id] + params,
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")


# ── User profile (Phase 2) ────────────────────────────────
@app.get("/api/profile", response_model=ProfileOut)
def get_profile(user=Depends(get_current_user)):
    return _profile_to_out(_load_profile(user["id"]))


@app.put("/api/profile", response_model=ProfileOut)
def update_profile(req: ProfileUpdate, user=Depends(get_current_user)):
    updates = req.model_dump(exclude_none=True)
    if "interests" in updates:
        updates["interests"] = _json.dumps(updates["interests"], ensure_ascii=False)
    if "share_with_agents" in updates:
        updates["share_with_agents"] = int(updates["share_with_agents"])

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id=?", (user["id"],)
        ).fetchone()
        if not existing:
            cols = ["user_id"] + list(updates.keys())
            placeholders = ",".join(["?"] * len(cols))
            conn.execute(
                f"INSERT INTO user_profiles ({','.join(cols)}) VALUES ({placeholders})",
                [user["id"]] + list(updates.values()),
            )
        elif updates:
            set_clause = ", ".join(f"{k}=?" for k in updates) + ", updated_at=CURRENT_TIMESTAMP"
            conn.execute(
                f"UPDATE user_profiles SET {set_clause} WHERE user_id=?",
                list(updates.values()) + [user["id"]],
            )
    return _profile_to_out(_load_profile(user["id"]))


@app.delete("/api/profile", status_code=204)
def delete_profile(user=Depends(get_current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM user_profiles WHERE user_id=?", (user["id"],))


# ── User facts (Phase 3) ──────────────────────────────────
@app.get("/api/facts", response_model=list[FactOut])
def list_facts(category: str | None = None, user=Depends(get_current_user)):
    sql = ("SELECT id, agent_id, category, key, value, confidence, created_at, updated_at "
           "FROM user_facts WHERE user_id=?")
    params: list = [user["id"]]
    if category:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY updated_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [FactOut(**dict(r)) for r in rows]


@app.delete("/api/facts/{fact_id}", status_code=204)
def delete_fact(fact_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM user_facts WHERE id=? AND user_id=?",
            (fact_id, user["id"]),
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Fact not found")


@app.delete("/api/facts", status_code=204)
def delete_all_facts(user=Depends(get_current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM user_facts WHERE user_id=?", (user["id"],))


# ── Image upload ───────────────────────────────────────────
from fastapi import UploadFile, File
from PIL import Image
import io

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

COVER_MAX_W, COVER_MAX_H = 1600, 600  # 封面圖最大尺寸（建議 3:1 比例上傳）
AVATAR_MAX_W, AVATAR_MAX_H = 256, 256
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _resize_and_save(data: bytes, kind: str) -> tuple[str, int, int, int]:
    """縮放圖片，回傳 (檔名, width, height, 檔案大小bytes)"""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    max_w, max_h = (AVATAR_MAX_W, AVATAR_MAX_H) if kind == "avatar" else (COVER_MAX_W, COVER_MAX_H)
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    # 輸出 WebP 兼顧品質與壓縮
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=85)
    filename = f"{uuid.uuid4().hex}.webp"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(out.getvalue())
    return filename, img.width, img.height, len(out.getvalue())


@app.post("/api/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    kind: str = "cover",          # "cover" | "avatar"
    user=Depends(get_current_user),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{file.content_type}")
    # M4 fix: 先用 Content-Length header 快速拒絕，避免整個大檔案被讀入記憶體
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="圖片不可超過 10 MB")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="圖片不可超過 10 MB")
    try:
        filename, w, h, size = _resize_and_save(data, kind)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"圖片處理失敗：{e}")
    return {
        "url": f"/static/uploads/{filename}",
        "width": w,
        "height": h,
        "size_kb": round(size / 1024, 1),
        "display": f"{w}×{h} px，{round(size/1024, 1)} KB",
    }


# ── Image proxy（解決 Google Drive / 外部圖片 CORS 問題）──────
import httpx as _httpx
from fastapi import Request as _Request
from fastapi.responses import Response as _Response
from urllib.parse import urlparse as _urlparse

_ALLOWED_IMAGE_HOSTS = {
    "lh3.googleusercontent.com",
    "drive.google.com",
    "storage.googleapis.com",
    "i.imgur.com",
    "images.unsplash.com",
}

# ── Knowledge Base endpoints ──────────────────────────────────────────────────

@app.post("/api/kb/documents", status_code=201)
def create_kb_document(req: KBDocumentCreate, user=Depends(get_current_user)):
    """Upload a raw document into the knowledge base (not yet compiled)."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO kb_documents (user_id, title, content, source_type, source_ref, tags)
               VALUES (?,?,?,?,?,?)""",
            (user["id"], req.title, req.content, req.source_type,
             req.source_ref, _json.dumps(req.tags, ensure_ascii=False)),
        )
    return {"id": cur.lastrowid, "message": "Document added. POST /api/kb/compile to process."}


@app.get("/api/kb/documents", response_model=list[KBDocumentOut])
def list_kb_documents(user=Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_documents WHERE user_id=? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = _parse_json_field(d.get("tags"), [])
        d["compiled"] = bool(d.get("compiled", 0))
        out.append(KBDocumentOut(**d))
    return out


@app.delete("/api/kb/documents/{doc_id}", status_code=204)
def delete_kb_document(doc_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM kb_documents WHERE id=? AND user_id=?", (doc_id, user["id"])
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Document not found")


@app.post("/api/kb/compile")
async def compile_kb(req: KBCompileRequest, user=Depends(get_current_user)):
    """Compile uncompiled documents into summaries and concepts (uses your first agent's LLM)."""
    with get_conn() as conn:
        if req.agent_id:
            agent = conn.execute(
                "SELECT * FROM agents WHERE id=? AND owner_id=?", (req.agent_id, user["id"])
            ).fetchone()
        else:
            agent = conn.execute(
                "SELECT * FROM agents WHERE owner_id=? ORDER BY created_at DESC LIMIT 1",
                (user["id"],),
            ).fetchone()

        if not agent:
            raise HTTPException(status_code=400, detail="No agent configured. Create an agent first.")

        if req.doc_ids:
            placeholders = ",".join("?" * len(req.doc_ids))
            docs = conn.execute(
                f"SELECT * FROM kb_documents WHERE user_id=? AND id IN ({placeholders})",
                [user["id"]] + list(req.doc_ids),
            ).fetchall()
        else:
            docs = conn.execute(
                "SELECT * FROM kb_documents WHERE user_id=? AND compiled=0 ORDER BY created_at ASC LIMIT 10",
                (user["id"],),
            ).fetchall()

    agent = dict(agent)
    try:
        runtime_api_key = decrypt_llm_api_key(agent.get("llm_api_key"))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    compiled_ids: list[int] = []
    for doc in docs:
        result = await compile_document(
            document_id=doc["id"],
            user_id=user["id"],
            provider=agent["llm_provider"],
            model=agent["llm_model"],
            base_url=agent["llm_base_url"],
            api_key=runtime_api_key,
        )
        if result:
            compiled_ids.append(doc["id"])

    return {"compiled": compiled_ids, "count": len(compiled_ids)}


@app.get("/api/kb/summaries", response_model=list[KBSummaryOut])
def list_kb_summaries(user=Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_summaries WHERE user_id=? ORDER BY updated_at DESC",
            (user["id"],),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = _parse_json_field(d.get("tags"), [])
        out.append(KBSummaryOut(**d))
    return out


@app.get("/api/kb/concepts", response_model=list[KBConceptOut])
def list_kb_concepts(user=Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_concepts WHERE user_id=? ORDER BY source_count DESC, updated_at DESC",
            (user["id"],),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = _parse_json_field(d.get("sources"), [])
        out.append(KBConceptOut(**d))
    return out


@app.get("/api/kb/concepts/{name}")
def get_kb_concept(name: str, user=Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_concepts WHERE user_id=? AND name=?", (user["id"], name)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Concept not found")
    d = dict(row)
    d["sources"] = _parse_json_field(d.get("sources"), [])
    return d


@app.get("/api/kb/search")
def search_knowledge_base(q: str, user=Depends(get_current_user)):
    """Keyword search across KB summaries and concepts."""
    return search_kb(user["id"], q)


# ── Memory endpoints ──────────────────────────────────────────────────────────

@app.get("/api/memory/session/{conv_id}", response_model=SessionMemoryOut)
def get_session_memory(conv_id: int, user=Depends(get_current_user)):
    """Get short-term working memory for a specific conversation."""
    with get_conn() as conn:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conv_id, user["id"]),
        ).fetchone()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    items = load_session_memory(conv_id)
    return SessionMemoryOut(
        conversation_id=conv_id,
        items=[SessionMemoryItem(**i) for i in items],
    )


@app.get("/api/memory/long-term")
def get_long_term_memory(user=Depends(get_current_user)):
    """Get all long-term memory: facts + KB summaries + concepts."""
    with get_conn() as conn:
        facts = conn.execute(
            """SELECT category, key, value, confidence FROM user_facts
               WHERE user_id=? ORDER BY updated_at DESC LIMIT 50""",
            (user["id"],),
        ).fetchall()
        summaries = conn.execute(
            """SELECT id, title, core_conclusions, tags, origin, created_at FROM kb_summaries
               WHERE user_id=? ORDER BY updated_at DESC LIMIT 30""",
            (user["id"],),
        ).fetchall()
        concepts = conn.execute(
            """SELECT name, definition, source_count, updated_at FROM kb_concepts
               WHERE user_id=? ORDER BY source_count DESC LIMIT 30""",
            (user["id"],),
        ).fetchall()
    return {
        "facts": [dict(r) for r in facts],
        "summaries": [dict(r) for r in summaries],
        "concepts": [dict(r) for r in concepts],
    }


@app.get("/api/imgproxy")
async def img_proxy(url: str):
    """後端圖片代理，繞過瀏覽器 CORS 限制。只允許白名單域名。"""
    parsed = _urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")
    if parsed.netloc not in _ALLOWED_IMAGE_HOSTS:
        raise HTTPException(status_code=403, detail=f"Host not allowed: {parsed.netloc}")
    try:
        async with _httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg")
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="URL is not an image")
        return _Response(
            content=resp.content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except _httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {e}")
