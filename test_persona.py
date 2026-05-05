import pytest
import requests

API_URL = "http://localhost:8000/api/chat/l4klxzx6cy"
HEADERS = {"Content-Type": "application/json"}

@pytest.fixture(scope="module")
def messages():
    # 產生 21 則訊息
    return [{"role": "user", "content": f"這是第{i+1}則訊息"} for i in range(21)]

def test_persona_update_and_context(messages):
    persona_update_count = 0
    for i in range(21):
        payload = {
            "messages": messages[max(0, i-4):i+1]  # 只送最新5則
        }
        resp = requests.post(API_URL, json=payload, headers=HEADERS)
        assert resp.status_code == 200
        found_extract = False
        for line in resp.text.splitlines():
            # 觀察伺服器回傳流中是否有 extract_facts debug log
            if "extract_facts" in line or "facts" in line:
                found_extract = True
        # 第20次（i==19）才應該觸發 persona 更新
        if i == 19:
            assert found_extract, f"第20次應該觸發 persona 更新，但沒有！"
            persona_update_count += 1
        else:
            assert not found_extract, f"第{i+1}次不應該觸發 persona 更新，但有！"
    assert persona_update_count == 1, "persona 應只更新一次"

# 執行方式：
# pytest test_persona.py
# 並觀察伺服器 log，確認 messages 表有 21 則訊息
