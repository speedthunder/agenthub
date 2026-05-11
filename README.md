# AgentHub

企業級開源多 Agent 平台，讓每位員工都能建立並管理自己的 AI 助理，並擁有持續成長的長短期記憶。

---

## 目錄

- [專案簡介](#專案簡介)
- [功能特色](#功能特色)
- [記憶架構](#記憶架構)
- [快速開始](#快速開始)
- [環境變數](#環境變數)
- [API 文件](#api-文件)
- [目錄結構](#目錄結構)
- [支援的 LLM Provider](#支援的-llm-provider)
- [如何貢獻](#如何貢獻)

---

## 專案簡介

AgentHub 是一個以 **FastAPI + SQLite + LLM** 為基礎的自架式多代理人平台。每位使用者可以建立多個 AI Agent，設定不同的系統提示、LLM 模型與參數。對話過程中，平台會自動累積使用者的長短期記憶，讓每次對話都比上一次更了解你。

靈感來源：
- Agent 平台架構參考 [Lobster](https://github.com/)
- Knowledge Base 記憶設計參考 [llm-knowledge-base](https://github.com/gatelynch/llm-knowledge-base)

---

## 功能特色

### Agent 管理
- 建立、編輯、刪除個人專屬 Agent
- 每個 Agent 可設定獨立的 LLM 供應商、模型、系統提示、溫度、Token 上限
- 支援公開分享（`/share/{slug}`）或私人使用
- 封面圖片、頭像、主題色、歡迎訊息、快捷技能選單

### 對話系統
- 串流式回應（Server-Sent Events）
- 完整對話歷史保存（登入用戶與匿名 session 皆支援）
- 可跨裝置繼續同一段對話

### 四層記憶系統

| 層級 | 範圍 | 說明 |
|------|------|------|
| 短期記憶 | 當次對話 | 工作記憶（當前任務、限制、進度狀態） |
| 長期 Tier 1 | 跨對話 | 使用者事實（姓名、職業、偏好等 key-value） |
| 長期 Tier 2–3 | 跨對話 | KB 文件與 LLM 編譯摘要 |
| 長期 Tier 4 | 跨對話 | 概念條目（出現 ≥2 次自動建立，持續更新） |

### 知識庫（Knowledge Base）
- 使用者可上傳任意文字文件（文章、筆記、說明書）
- 一鍵觸發 LLM 編譯：自動生成摘要、關鍵證據、疑點、關鍵詞
- 自動萃取跨文件概念，建立個人知識圖譜
- 每次對話自動將對話內容編譯進知識庫（累積式）
- 支援關鍵字搜尋摘要與概念
- **完整前端管理介面**（文件 / 摘要 / 概念 / 搜尋 四個分頁）

### 個人設定（Persona）
- 語言偏好、時區、回覆風格、語氣、Emoji 設定
- 自訂補充說明，注入每次對話的 system prompt
- 可一鍵關閉個人資訊分享

### 圖片上傳
- 支援 JPEG / PNG / WebP / GIF
- 自動縮放並轉換為 WebP（封面 1600×600，頭像 256×256）
- 最大 10 MB

---

## 記憶架構

```
每次對話開始（system prompt 自動注入）:
┌─────────────────────────────────────────────────────┐
│  [Agent system prompt]                              │
│  [About the user]     ← Persona（偏好/語言/語氣）    │
│  [Known facts]        ← 長期 Tier 1：key-value facts │
│  [Long-term knowledge]← 長期 Tier 3-4：KB 摘要+概念  │
│  [Working memory]     ← 短期：當次對話工作記憶        │
└─────────────────────────────────────────────────────┘

對話進行中（串流結束後背景自動執行）:
  每 1 輪   → 更新短期 session working memory
  每 1 輪   → 萃取長期 user facts（Tier 1）
  每 5 輪   → 把對話編譯進 KB summaries（Tier 3）
              └─ 自動更新 KB concepts（Tier 4）
```

---

## 快速開始

**需求：Python 3.10+**

```bash
# 1. clone 專案
git clone https://github.com/speedthunder/agenthub.git
cd agenthub

# 2. 安裝依賴
pip install -r agent_platform/requirements.txt

# 3. 啟動
python -m agent_platform.main

# 4. 開啟瀏覽器
# http://localhost:8000/agenthub/static/index.html
```

**使用 Docker：**

```bash
docker build -t agenthub .
docker run -p 8000:8000 agenthub
```

---

## 環境變數

在專案根目錄建立 `.env` 檔案可覆蓋預設值：

| 變數名稱 | 預設值 | 說明 |
|----------|--------|------|
| `DB_PATH` | `agents.db` | SQLite 資料庫路徑 |
| `CONTEXT_WINDOW_DIALOGS` | `5` | 傳入 LLM 的最近對話輪數 |
| `PERSONA_UPDATE_INTERVAL` | `2` | 每幾則訊息萃取一次長期 facts（2 = 每輪都萃取） |
| `SESSION_MEMORY_INTERVAL` | `2` | 每幾則訊息更新一次短期記憶 |
| `KB_COMPILE_INTERVAL` | `10` | 每幾則訊息把對話編譯進 KB |

---

## API 文件

啟動後可至 `http://localhost:8000/docs` 查看完整 Swagger 文件。

### 認證

```
POST /auth/register   建立帳號
POST /auth/login      登入，取得 JWT token
GET  /auth/me         查詢目前使用者
```

### Agent

```
POST   /agents              建立 Agent
GET    /agents              列出我的所有 Agent
GET    /agents/{id}         取得單一 Agent
PUT    /agents/{id}         更新 Agent
DELETE /agents/{id}         刪除 Agent
GET    /share/{slug}        Agent 公開分享頁
POST   /api/chat/{slug}     對話（串流，SSE）
```

### 對話歷史

```
GET    /api/conversations                     列出對話紀錄
GET    /api/conversations/{id}/messages       取得對話訊息
DELETE /api/conversations/{id}               刪除對話
```

### 個人設定

```
GET    /api/profile    取得設定
PUT    /api/profile    更新設定
DELETE /api/profile    清除設定
```

### 記憶管理

```
GET    /api/facts                 列出長期 facts（Tier 1）
DELETE /api/facts/{id}            刪除單一 fact
DELETE /api/facts                 清除全部 facts

GET    /api/memory/session/{conv_id}   短期記憶：查詢當次對話工作記憶
GET    /api/memory/long-term           長期記憶：facts + KB 摘要 + 概念
```

### 知識庫（Knowledge Base）

```
POST   /api/kb/documents          上傳原始文件
GET    /api/kb/documents          列出所有文件
DELETE /api/kb/documents/{id}     刪除文件

POST   /api/kb/compile            觸發 LLM 編譯（生成摘要+概念）
GET    /api/kb/summaries          列出所有編譯摘要
GET    /api/kb/concepts           列出所有概念條目
GET    /api/kb/concepts/{name}    取得單一概念詳情
GET    /api/kb/search?q={關鍵字}  搜尋摘要與概念
```

### 媒體

```
POST /api/upload/image    上傳圖片（自動轉 WebP）
GET  /api/imgproxy        外部圖片代理（CORS 解決方案）
```

---

## 目錄結構

```
agenthub/
├── agent_platform/
│   ├── app.py              FastAPI 主應用程式（所有 endpoints）
│   ├── database.py         資料庫 schema 與連線（10 張資料表）
│   ├── schemas.py          Pydantic 資料模型
│   ├── auth.py             JWT 認證工具
│   ├── config.py           環境變數設定
│   ├── llm_bridge.py       LLM 串流橋接層
│   ├── persona.py          長期 facts 萃取（Tier 1）
│   ├── knowledge_base.py   知識庫編譯與管理（Tier 2-4）
│   ├── memory_manager.py   短期 session 記憶
│   ├── main.py             進入點
│   └── static/
│       ├── index.html      Agent 管理主介面
│       ├── chat.html       對話介面（公開分享用）
│       ├── profile.html    個人設定頁
│       ├── memory.html     記憶管理頁
│       └── kb.html         知識庫管理頁
├── test_e2e.py             端到端測試（Phase 1-3 全流程）
├── Dockerfile
└── README.md
```

### 資料庫資料表

| 資料表 | 說明 |
|--------|------|
| `users` | 使用者帳號 |
| `agents` | Agent 設定 |
| `conversations` | 對話紀錄 |
| `messages` | 訊息內容 |
| `user_profiles` | 個人偏好設定 |
| `user_facts` | 長期 facts（Tier 1） |
| `session_memory` | 短期 session 記憶 |
| `kb_documents` | KB 原始文件（Tier 2） |
| `kb_summaries` | KB 編譯摘要（Tier 3） |
| `kb_concepts` | KB 概念條目（Tier 4） |

---

## 支援的 LLM Provider

| Provider | 設定方式 |
|----------|----------|
| **Ollama**（本機） | `base_url=http://localhost:11434` |
| **OpenAI** | `base_url=https://api.openai.com/v1`，填入 API key |
| **Azure OpenAI** | 填入 Azure endpoint 與 api-key |

---

## 介面預覽

主畫面：
![主畫面](agent_platform/static/screenshots/main.jpg)

建立/編輯 Agent：
![編輯 Agent](agent_platform/static/screenshots/edit.jpg)

對話介面：
![對話](agent_platform/static/screenshots/talk.jpg)

個人設定（Persona）：
![個人設定](agent_platform/static/screenshots/persona.jpg)

對話歷史：
![歷史紀錄](agent_platform/static/screenshots/history.jpg)

---

## 如何貢獻

1. Fork 本 repo
2. 建立 feature branch：`git checkout -b feature/my-feature`
3. 提交 PR，說明改動目的

**歡迎貢獻的方向：**
- 更多 LLM provider 支援（Gemini、Claude、Cohere）
- 多租戶 / 組織權限管理
- 概念條目的「張力與缺口」自動偵測
- 測試覆蓋率提升

---

MIT License
