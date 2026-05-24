import asyncio
import os
import tempfile
import time

from fastapi.testclient import TestClient

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as _tmp_db:
    TMP_DB = _tmp_db.name
os.environ["DB_PATH"] = TMP_DB
os.environ["JWT_SECRET"] = "test-secret"
os.environ["APP_SECRET_KEY"] = "test-app-secret-key"

from agent_platform import app as app_module
from agent_platform.database import get_conn, init_db
from agent_platform.knowledge_base import _llm_complete
from agent_platform.memory_manager import extract_session_memory
from agent_platform.persona import extract_facts

init_db()
client = TestClient(app_module.app)
EMAIL = f"security_{int(time.time())}@test.com"


def _register_and_login():
    r = client.post("/auth/register", json={
        "username": f"user_{int(time.time())}",
        "email": EMAIL,
        "password": "secret1234",
    })
    assert r.status_code == 201
    return r


def test_cookie_auth_logout_revocation():
    r = _register_and_login()
    token = r.json()["access_token"]
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    me = client.get("/auth/me")
    assert me.status_code == 200

    out = client.post("/auth/logout")
    assert out.status_code == 204

    me_old = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_old.status_code == 401


def test_login_rate_limit():
    app_module.LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 3
    app_module.LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300
    app_module.LOGIN_RATE_LIMIT_BLOCK_SECONDS = 300
    app_module._login_attempts.clear()

    for _ in range(3):
        bad = client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"})
        assert bad.status_code == 401
    blocked = client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert blocked.status_code == 429


def test_llm_api_key_encrypted_and_not_overwritten_by_blank_update():
    app_module._login_attempts.clear()
    ok = client.post("/auth/login", json={"email": EMAIL, "password": "secret1234"})
    assert ok.status_code == 200

    created = client.post("/agents", json={
        "name": "SecAgent",
        "system_prompt": "You are secure.",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_api_key": "sk-test-secret",
    })
    assert created.status_code == 201
    agent_id = created.json()["id"]

    with get_conn() as conn:
        row = conn.execute("SELECT llm_api_key FROM agents WHERE id=?", (agent_id,)).fetchone()
        enc_before = row["llm_api_key"]
    assert enc_before.startswith("enc:v1:")
    assert "sk-test-secret" not in enc_before

    updated = client.put(f"/agents/{agent_id}", json={"name": "SecAgent2", "llm_api_key": ""})
    assert updated.status_code == 200

    with get_conn() as conn:
        row2 = conn.execute("SELECT llm_api_key FROM agents WHERE id=?", (agent_id,)).fetchone()
    assert row2["llm_api_key"] == enc_before


def test_blocked_base_url_applies_to_all_llm_helpers():
    facts = asyncio.run(extract_facts(
        provider="ollama",
        model="x",
        base_url="http://127.0.0.1:11434",
        api_key="",
        user_message="hi",
        assistant_message="ok",
    ))
    assert facts == []

    items = asyncio.run(extract_session_memory(
        provider="ollama",
        model="x",
        base_url="http://127.0.0.1:11434",
        api_key="",
        user_message="hi",
        assistant_message="ok",
    ))
    assert items == []

    try:
        asyncio.run(_llm_complete(
            provider="ollama",
            model="x",
            base_url="http://127.0.0.1:11434",
            api_key="",
            messages=[],
        ))
        assert False, "Expected blocked base_url to raise"
    except ValueError as e:
        assert "Blocked internal host" in str(e)
