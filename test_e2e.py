"""端到端測試:涵蓋 Phase 1-3 全部流程
用 monkeypatch 取代 LLM 呼叫,不需要實際 LLM 服務
"""
import os
import sys
import asyncio
import tempfile
import time

# 用獨立 DB
TMP_DB = tempfile.mktemp(suffix=".db")
os.environ["DB_PATH"] = TMP_DB
os.environ["JWT_SECRET"] = "test-secret"
os.environ["APP_SECRET_KEY"] = "test-app-secret-key"
USERNAME = f"alice_{int(time.time())}"
EMAIL = f"{USERNAME}@test.com"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from agent_platform import app as app_module
from agent_platform import persona as persona_module
from agent_platform import llm_bridge as llm_module

# ── Mock LLM ──────────────────────────────────────────────
captured_system_prompts = []   # 收集每次 chat 用到的 system_prompt

async def fake_stream_chat(*, provider, model, base_url, api_key,
                           system_prompt, messages, temperature, max_tokens):
    captured_system_prompts.append(system_prompt)
    # 模擬回覆
    for tok in ["你好", ",", "我", "聽到", "你了"]:
        yield tok

# 模擬萃取:看到「我叫 X」就回傳 identity.name=X
async def fake_extract_facts(*, provider, model, base_url, api_key,
                             user_message, assistant_message):
    import re
    facts = []
    m = re.search(r"我叫\s*(\S+)", user_message)
    if m:
        facts.append({"category": "identity", "key": "name",
                      "value": m.group(1), "confidence": 0.95})
    m = re.search(r"養了?(?:一隻)?(?:貓|狗)叫\s*(\S+)", user_message)
    if m:
        facts.append({"category": "family", "key": "pet_name",
                      "value": m.group(1), "confidence": 0.9})
    return facts

# ── Patch ──
app_module.stream_chat = fake_stream_chat
app_module.extract_facts = fake_extract_facts
llm_module.stream_chat = fake_stream_chat
persona_module.extract_facts = fake_extract_facts

client = TestClient(app_module.app)

# TestClient 預設不跑 lifespan(init_db 不會自動執行),手動呼叫
from agent_platform.database import init_db as _init_db
_init_db()

# 診斷:確認用的是 tmp DB
from agent_platform import database as _d
print(f"[setup] DB_PATH = {_d.DB_PATH}")
print(f"[setup] expected = {TMP_DB}")
print(f"[setup] DB exists before register: {os.path.exists(TMP_DB)}")

def section(title):
    print("\n" + "=" * 60)
    print("  " + title)
    print("=" * 60)

def assert_eq(actual, expected, label):
    ok = actual == expected
    sym = "✓" if ok else "✗"
    print(f"  {sym} {label}: got={actual!r}")
    if not ok:
        print(f"    EXPECTED: {expected!r}")
        sys.exit(1)

def assert_in(needle, haystack, label):
    ok = needle in (haystack or "")
    sym = "✓" if ok else "✗"
    print(f"  {sym} {label}")
    if not ok:
        print(f"    needle={needle!r}\n    haystack={haystack!r}")
        sys.exit(1)

def assert_not_in(needle, haystack, label):
    ok = needle not in (haystack or "")
    sym = "✓" if ok else "✗"
    print(f"  {sym} {label}")
    if not ok:
        print(f"    UNEXPECTED needle={needle!r} found in {haystack!r}")
        sys.exit(1)

# ── 1. 註冊 + 登入 ─────────────────────────────────────────
section("1. 註冊")
r = client.post("/auth/register", json={
    "username": USERNAME, "email": EMAIL, "password": "secret123"
})
assert_eq(r.status_code, 201, "register status")
TOKEN = r.json()["access_token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
print(f"  ✓ token={TOKEN[:20]}...")

r = client.get("/auth/me", headers=AUTH)
assert_eq(r.json()["username"], USERNAME, "/auth/me username")

# ── 2. 建立 agent ──────────────────────────────────────────
section("2. 建立 agent")
r = client.post("/agents", json={
    "name": "TestBot",
    "system_prompt": "You are a test bot.",
    "is_public": True,
}, headers=AUTH)
assert_eq(r.status_code, 201, "create agent")
agent = r.json()
SLUG = agent["slug"]
print(f"  ✓ slug={SLUG}, agent_id={agent['id']}")

# ── 3. Profile ─────────────────────────────────────────────
section("3. Profile CRUD")
r = client.get("/api/profile", headers=AUTH)
assert_eq(r.status_code, 200, "get profile")
assert_eq(r.json()["display_name"], "", "default empty")

r = client.put("/api/profile", json={
    "display_name": "Alice大大",
    "language": "zh-TW",
    "tone": "casual",
    "use_emoji": 0,
    "interests": ["Python", "貓"],
    "custom_instructions": "請用台語回覆",
    "share_with_agents": True,
}, headers=AUTH)
assert_eq(r.status_code, 200, "update profile")
assert_eq(r.json()["display_name"], "Alice大大", "saved name")
assert_eq(r.json()["interests"], ["Python", "貓"], "saved interests")
assert_eq(r.json()["share_with_agents"], True, "share flag true")

# ── 4. Chat (登入 + persona 注入) ──────────────────────────
section("4. Chat 注入 persona")
captured_system_prompts.clear()
r = client.post(f"/api/chat/{SLUG}", json={
    "messages": [{"role": "user", "content": "我叫小明"}],
}, headers={**AUTH, "Content-Type": "application/json"})
assert_eq(r.status_code, 200, "chat status")
conv_id = int(r.headers.get("X-Conversation-Id", "0"))
print(f"  ✓ conversation_id={conv_id}")
assert conv_id > 0, "should have new conversation id"

# 確認串流內容
body = r.text
assert_in("data: 你好", body, "stream contains content")
assert_in("data: [DONE]", body, "stream ends with DONE")

# 確認 system_prompt 注入
sp = captured_system_prompts[0]
assert_in("You are a test bot.", sp, "original prompt preserved")
assert_in("[About the user]", sp, "persona block injected")
assert_in("Alice大大", sp, "display_name injected")
assert_in("Python", sp, "interests injected")
assert_in("請用台語回覆", sp, "custom_instructions injected")
assert_in("Do NOT use emoji", sp, "emoji=no injected")

# ── 5. 對話持久化 ─────────────────────────────────────────
section("5. 對話與訊息已落地")
r = client.get(f"/api/conversations?agent_slug={SLUG}", headers=AUTH)
convs = r.json()
assert_eq(len(convs), 1, "one conversation")
assert_eq(convs[0]["id"], conv_id, "conv id matches")
assert_in("我叫小明", convs[0]["title"], "title from first user msg")

r = client.get(f"/api/conversations/{conv_id}/messages", headers=AUTH)
msgs = r.json()
assert_eq(len(msgs), 2, "user + assistant stored")
assert_eq(msgs[0]["role"], "user", "first is user")
assert_eq(msgs[0]["content"], "我叫小明", "user content")
assert_eq(msgs[1]["role"], "assistant", "second is assistant")
assert_in("你好", msgs[1]["content"], "assistant content")

# ── 6. Facts 萃取 ─────────────────────────────────────────
section("6. Facts 自動萃取")
r = client.get("/api/facts", headers=AUTH)
facts = r.json()
print(f"  facts: {[(f['category'], f['key'], f['value']) for f in facts]}")
assert_eq(len(facts), 1, "one fact extracted")
assert_eq(facts[0]["category"], "identity", "category=identity")
assert_eq(facts[0]["key"], "name", "key=name")
assert_eq(facts[0]["value"], "小明", "value=小明")

# 第二輪:加更多 facts(同對話續寫)
captured_system_prompts.clear()
r = client.post(f"/api/chat/{SLUG}", json={
    "messages": [
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好,我聽到你了"},
        {"role": "user", "content": "我養了一隻貓叫小白"},
    ],
    "conversation_id": conv_id,
}, headers={**AUTH, "Content-Type": "application/json"})
assert_eq(r.status_code, 200, "second chat status")

# 確認第二輪有看到上一輪萃取的 fact
sp2 = captured_system_prompts[0]
assert_in("[Known facts about the user", sp2, "facts block injected on round 2")
assert_in("identity.name: 小明", sp2, "previous fact injected")

# ── 7. 第三輪 chat:facts 更新 ─────────────────────────────
section("7. Facts upsert (新貓 fact)")
r = client.get("/api/facts", headers=AUTH)
facts = r.json()
keys = sorted([(f["category"], f["key"], f["value"]) for f in facts])
print(f"  facts now: {keys}")
assert ("family", "pet_name", "小白") in keys, "pet fact extracted"
assert ("identity", "name", "小明") in keys, "name fact still there"
assert_eq(len(facts), 2, "two facts total")

# ── 8. 訪客模式不受影響 ───────────────────────────────────
section("8. 訪客模式")
captured_system_prompts.clear()
r = client.post(f"/api/chat/{SLUG}", json={
    "messages": [{"role": "user", "content": "hi"}],
}, headers={"Content-Type": "application/json", "X-Anon-Session": "guestabcdef1234"})
assert_eq(r.status_code, 200, "guest chat ok")
sp = captured_system_prompts[0]
assert_not_in("[About the user]", sp, "guest gets NO profile")
assert_not_in("[Known facts", sp, "guest gets NO facts")
assert_in("You are a test bot.", sp, "guest still gets agent prompt")

# guest 也有自己的 conversation
guest_conv = int(r.headers.get("X-Conversation-Id", "0"))
r = client.get(f"/api/conversations?agent_slug={SLUG}",
               headers={"X-Anon-Session": "guestabcdef1234"})
assert_eq(len(r.json()), 1, "guest sees only own conversation")
print(f"  ✓ guest conv_id={guest_conv}, separated from logged-in user")

# ── 9. share_with_agents=False 隱私開關 ───────────────────
section("9. 關閉分享開關 → profile/facts 全部不注入")
client.put("/api/profile", json={"share_with_agents": False}, headers=AUTH)
captured_system_prompts.clear()
r = client.post(f"/api/chat/{SLUG}", json={
    "messages": [{"role": "user", "content": "我叫小華"}],
}, headers={**AUTH, "Content-Type": "application/json"})
sp = captured_system_prompts[0]
assert_not_in("[About the user]", sp, "no persona when off")
assert_not_in("[Known facts", sp, "no facts when off")
assert_not_in("Alice大大", sp, "name NOT leaked")

# 萃取也應跳過(facts 數量不變)
r = client.get("/api/facts", headers=AUTH)
assert_eq(len(r.json()), 2, "facts NOT increased when share=off")

# 還原
client.put("/api/profile", json={"share_with_agents": True}, headers=AUTH)

# ── 10. 刪除 fact / 對話 ──────────────────────────────────
section("10. 刪除操作")
r = client.get("/api/facts", headers=AUTH)
fact_id = r.json()[0]["id"]
r = client.delete(f"/api/facts/{fact_id}", headers=AUTH)
assert_eq(r.status_code, 204, "delete fact")
r = client.get("/api/facts", headers=AUTH)
assert_eq(len(r.json()), 1, "one fact left")

r = client.delete(f"/api/conversations/{conv_id}", headers=AUTH)
assert_eq(r.status_code, 204, "delete conversation")
r = client.get(f"/api/conversations/{conv_id}/messages", headers=AUTH)
assert_eq(r.status_code, 404, "conv 404 after delete")

# ── 11. Ownership check ───────────────────────────────────
section("11. Ownership 防護")
# 別人的 conversation 不能讀
r = client.get(f"/api/conversations/{guest_conv}/messages", headers=AUTH)
assert_eq(r.status_code, 404, "logged-in user cannot read guest conv")
r = client.get(f"/api/conversations/{guest_conv}/messages",
               headers={"X-Anon-Session": "another-guest-99999"})
assert_eq(r.status_code, 404, "other guest cannot read")

# ── 12. 敏感詞過濾(persona 模組) ─────────────────────────
section("12. 敏感詞過濾")
out = persona_module._parse_and_validate(
    '[{"category":"misc","key":"my_password","value":"abc123","confidence":0.9}]'
)
assert_eq(out, [], "password key blocked")
out = persona_module._parse_and_validate(
    '[{"category":"misc","key":"x","value":"my api_key is sk-xxx","confidence":0.9}]'
)
assert_eq(out, [], "api_key in value blocked")

print("\n" + "=" * 60)
print("  🎉 所有測試通過")
print("=" * 60)
print(f"  測試 DB: {TMP_DB}")
os.unlink(TMP_DB)
