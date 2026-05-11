# AgentHub

<div align="center">

**企業級開源多 Agent 平台**
讓每位員工都能建立並管理自己的 AI 助理，擁有持續成長的長短期記憶與個人知識庫。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>

---

## 目錄

- [專案簡介](#專案簡介)
- [功能特色](#功能特色)
- [記憶與知識庫架構](#記憶與知識庫架構)
- [快速開始](#快速開始)
- [環境變數](#環境變數)
- [介面說明](#介面說明)
- [API 文件](#api-文件)
- [目錄結構](#目錄結構)
- [支援的 LLM Provider](#支援的-llm-provider)
- [如何貢獻](#如何貢獻)

---

## 專案簡介

AgentHub 是一個以 **FastAPI + SQLite + LLM** 為基礎的自架式多代理人平台。

每位使用者可建立多個 AI Agent，設定專屬的系統提示、LLM 模型與參數。對話過程中，平台會自動：

- 從對話中萃取長期使用者事實（姓名、職業、偏好等）
- 維護短期工作記憶（當次對話的任務狀態）
- 將對話內容累積進個人知識庫
- **依當前問題動態檢索相關知識，讓 LLM 在回答前先查閱知識庫並標注來源**

---

## 功能特色

### Agent 管理
- 建立、編輯、刪除個人專屬 Agent
- 每個 Agent 獨立設定 LLM 供應商、模型、溫度、Token 上限、系統提示
- 支援公開分享（`/share/{slug}`）或私人使用
- 封面圖片、頭像、主題色、歡迎訊息、快捷技能選單

### 對話系統
- 串流式回應（Server-Sent Events）
- 完整對話歷史保存，支援跨裝置繼續對話
- 登入用戶與匿名 session 皆支援

### 四層記憶系統

| 層級 | 觸發時機 | 說明 |
|------|----------|------|
| 短期記憶 | 每輪對話後 | 當次對話工作記憶（任務、進度、限制） |
| 長期 Tier 1 | 每輪對話後 | 使用者事實（姓名、職業、偏好等 key-value） |
| 長期 Tier 2–3 | 每 5 輪對話後 | 知識庫文件與 LLM 編譯摘要 |
| 長期 Tier 4 | 編譯時自動更新 | 跨文件概念條目 |

### 知識庫（Knowledge Base）

- **上傳文件**：貼上任意文章、筆記、說明書
- **LLM 一鍵編譯**：自動生成摘要、關鍵證據、疑點、關鍵詞，並萃取概念條目
- **對話自動累積**：每 5 輪對話後，自動將對話內容編譯進知識庫
- **RAG 動態檢索**：每次對話前，從使用者訊息提取關鍵詞（中文 n-gram + 英文單詞），搜尋相關摘要與概念，只注入有命中的內容
- **明確引用來源**：LLM 使用知識庫內容時，會主動說明出處（「根據你知識庫中的《X》…」）
- **前端管理介面**：文件 / 摘要 / 概念 / 搜尋 四個分頁

### 個人設定（Persona）
- 語言偏好、時區、回覆風格、語氣、Emoji 設定
- 自訂補充說明，注入每次對話的 system prompt
- 一鍵關閉個人資訊分享（確保隱私）

### 圖片上傳
- 支援 JPEG / PNG / WebP / GIF，最大 10 MB
- 自動縮放並轉換為 WebP（封面 1600×600，頭像 256×256）

---

## 記憶與知識庫架構

```
每次對話開始（system prompt 自動組裝）
┌────────────────────────────────────────────────────────┐
│  [Agent system prompt]                                 │
│  [About the user]      ← Persona（語言、語氣、偏好）    │
│  [Known facts]         ← Tier 1：key-value 長期事實    │
│  [Relevant knowledge]  ← KB 動態 RAG 檢索結果          │
│    IMPORTANT: cite sources when using KB entries       │
│  [Working memory]      ← 短期：當次對話工作記憶         │
└────────────────────────────────────────────────────────┘

對話結束後（背景自動執行）
  每輪    → 更新短期 session working memory
  每輪    → 萃取長期 user facts（Tier 1）
  每 5 輪 → 將對話編譯進 KB summaries（Tier 3）
             └─ 自動更新 KB concepts（Tier 4）

KB 動態 RAG 流程
  使用者訊息 → 提取關鍵詞 → OR LIKE 搜尋 kb_summaries + kb_concepts
             → 有命中：注入相關條目 + 引用指令
             → 無命中：不注入（省 token）
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
# http://localhost:8000
```

**使用 Docker：**

```bash
docker build -t agenthub .
docker run -p 8000:8000 agenthub
```

---

## 環境變數

在專案根目錄建立 `.env` 可覆蓋預設值：

| 變數名稱 | 預設值 | 說明 |
|----------|--------|------|
| `DB_PATH` | `agents.db` | SQLite 資料庫路徑 |
| `CONTEXT_WINDOW_DIALOGS` | `5` | 送入 LLM 的最近對話輪數 |
| `SESSION_MEMORY_INTERVAL` | `2` | 每幾則訊息更新一次短期記憶（2 = 每輪） |
| `PERSONA_UPDATE_INTERVAL` | `2` | 每幾則訊息萃取一次長期 facts（2 = 每輪） |
| `KB_COMPILE_INTERVAL` | `10` | 每幾則訊息把對話編譯進 KB（10 = 每 5 輪） |

---

## 介面說明

| 頁面 | 路徑 | 功能 |
|------|------|------|
| 主畫面 | `/agenthub/static/index.html` | 建立、管理、分享 Agent |
| 對話 | `/share/{slug}` | 與 Agent 對話（公開分享） |
| 知識庫 | `/agenthub/static/kb.html` | 上傳文件、觸發編譯、瀏覽摘要與概念 |
| 我的記憶 | `/agenthub/static/memory.html` | 查看／刪除 LLM 萃取的長期事實 |
| 個人設定 | `/agenthub/static/profile.html` | 設定語言、語氣、自訂指令 |

---

## API 文件

啟動後至 `http://localhost:8000/docs` 查看完整 Swagger 文件。

### 認證
```
POST /auth/register
POST /auth/login
GET  /auth/me
```

### Agent
```
POST   /agents
GET    /agents
GET    /agents/{id}
PUT    /agents/{id}
DELETE /agents/{id}
GET    /share/{slug}
POST   /api/chat/{slug}        串流對話（SSE）
```

### 對話歷史
```
GET    /api/conversations
GET    /api/conversations/{id}/messages
DELETE /api/conversations/{id}
```

### 個人設定
```
GET    /api/profile
PUT    /api/profile
DELETE /api/profile
```

### 記憶管理
```
GET    /api/facts
DELETE /api/facts/{id}
DELETE /api/facts
GET    /api/memory/session/{conv_id}
GET    /api/memory/long-term
```

### 知識庫
```
POST   /api/kb/documents
GET    /api/kb/documents
DELETE /api/kb/documents/{id}
POST   /api/kb/compile
GET    /api/kb/summaries
GET    /api/kb/concepts
GET    /api/kb/concepts/{name}
GET    /api/kb/search?q={keyword}
```

### 媒體
```
POST /api/upload/image
GET  /api/imgproxy
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
│   ├── knowledge_base.py   知識庫編譯、RAG 檢索（Tier 2–4）
│   ├── memory_manager.py   短期 session 記憶
│   ├── main.py             進入點（掛載於 /agenthub）
│   └── static/
│       ├── index.html      Agent 管理主介面
│       ├── chat.html       對話介面
│       ├── kb.html         知識庫管理介面
│       ├── memory.html     記憶管理介面
│       └── profile.html    個人設定介面
├── test_e2e.py             端到端測試（12 個場景全覆蓋）
├── Dockerfile
├── .gitignore
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
| **Ollama**（本機） | `llm_provider=ollama`，`base_url=http://localhost:11434` |
| **OpenAI** | `llm_provider=openai`，`base_url=https://api.openai.com/v1`，填入 API key |
| **Azure OpenAI** | `llm_provider=azure`，填入 Azure endpoint 與 api-key |

---

## 如何貢獻

1. Fork 本 repo
2. 建立 feature branch：`git checkout -b feature/my-feature`
3. 提交 PR，說明改動目的

**歡迎貢獻的方向：**
- 語意向量搜尋（取代目前的 LIKE 關鍵詞搜尋）
- 更多 LLM provider（Gemini、Claude、Cohere）
- 多租戶 / 組織權限管理
- 測試覆蓋率提升

---

MIT License
