# AgentHub

<div align="center">

**開源自架式多 Agent 平台**
讓每位使用者都能建立並管理自己的 AI 助理，擁有持續成長的長短期記憶與個人知識庫。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688)](https://fastapi.tiangolo.com/)
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
- [資料庫結構](#資料庫結構)
- [支援的 LLM Provider](#支援的-llm-provider)
- [部署到 Zeabur](#部署到-zeabur)
- [如何貢獻](#如何貢獻)

---

## 專案簡介

AgentHub 是以 **FastAPI + SQLite + LLM 串流** 為基礎的自架式多代理人平台。

每位使用者可建立多個 AI Agent，各自設定獨立的系統提示、LLM 模型與參數。對話過程中平台自動執行四層記憶機制：從對話萃取長期使用者事實、維護短期工作記憶、將內容累積進個人知識庫，並在每次對話前動態檢索相關知識注入 LLM。

---

## 功能特色

### Agent 管理
- 建立、編輯、刪除個人專屬 Agent
- 每個 Agent 獨立設定 LLM 供應商、模型、溫度、Token 上限、系統提示
- 支援公開分享（`/share/{slug}`）或私人使用
- 封面圖片、頭像、主題色、歡迎訊息、快捷提示選單

### 對話系統
- 串流式回應（Server-Sent Events）
- 完整對話歷史保存，支援跨裝置繼續
- 支援已登入使用者與匿名 session

### 四層記憶系統

| 層級 | 觸發時機 | 內容 |
|------|----------|------|
| 短期記憶 | 每輪對話後 | 當次對話工作記憶（任務、進度、限制） |
| 長期 Tier 1 | 每輪對話後 | 使用者事實（姓名、職業、偏好等 key-value） |
| 長期 Tier 2–3 | 每 5 輪對話後 | 知識庫文件與 LLM 編譯摘要 |
| 長期 Tier 4 | 編譯時自動更新 | 跨文件概念條目 |

### 知識庫（RAG）
- **上傳文件**：貼上任意文章、筆記、說明文字
- **LLM 一鍵編譯**：自動生成摘要、關鍵證據、疑點、關鍵詞，並萃取概念條目
- **對話自動累積**：每 5 輪對話後，自動將對話內容編譯進知識庫
- **動態 RAG 檢索**：每次對話前從使用者訊息提取關鍵詞，搜尋相關摘要與概念，只注入命中內容（省 token）
- **明確引用來源**：LLM 使用知識庫內容時會主動標注出處

### 個人設定（Persona）
- 語言偏好、時區、回覆風格、語氣、Emoji 設定
- 自訂補充說明，注入每次對話的 system prompt
- 一鍵關閉個人資訊分享

### 圖片上傳
- 支援 JPEG / PNG / WebP / GIF，最大 10 MB
- 自動縮放並轉換為 WebP（封面 1600×600，頭像 256×256）

### 認證
- Email + 密碼註冊／登入（bcrypt + SHA-256 prehash）
- Google OAuth 2.0（One Tap 自動彈出）
- JWT 認證，7 天有效期

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
  使用者訊息 → 提取關鍵詞（中文 n-gram + 英文單詞）
             → OR LIKE 搜尋 kb_summaries + kb_concepts
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

# 3. 建立環境變數（複製範本後填入真實值）
cp .env.example .env

# 4. 啟動
python -m agent_platform.main

# 5. 開啟瀏覽器
# http://localhost:8000
```

**使用 Docker：**

```bash
docker build -t agenthub .
docker run -p 8000:8000 --env-file .env agenthub
```

---

## 環境變數

複製 `.env.example` 為 `.env` 並填入以下值：

| 變數名稱 | 必填 | 預設值 | 說明 |
|----------|------|--------|------|
| `JWT_SECRET` | 建議 | 隨機生成 | JWT 簽名金鑰；未設定時重啟後所有 token 失效 |
| `GOOGLE_CLIENT_ID` | 選填 | `""` | Google OAuth Client ID；留空則不顯示 Google 登入按鈕 |
| `PORT` | 選填 | `8000` | 伺服器監聽 port |
| `DB_PATH` | 選填 | `agents.db` | SQLite 資料庫路徑 |
| `CONTEXT_WINDOW_DIALOGS` | 選填 | `5` | 送入 LLM 的最近對話輪數 |
| `SESSION_MEMORY_INTERVAL` | 選填 | `2` | 每幾則訊息更新一次短期記憶（2 = 每輪） |
| `PERSONA_UPDATE_INTERVAL` | 選填 | `2` | 每幾則訊息萃取一次長期 facts（2 = 每輪） |
| `KB_COMPILE_INTERVAL` | 選填 | `10` | 每幾則訊息把對話編譯進 KB（10 = 每 5 輪） |

產生安全的 `JWT_SECRET`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 介面說明

| 頁面 | 路徑 | 功能 |
|------|------|------|
| 主畫面 | `/` | 建立、管理、分享 Agent |
| 對話 | `/share/{slug}` | 與 Agent 對話（公開分享） |
| 知識庫 | `/agenthub/static/kb.html` | 上傳文件、觸發編譯、瀏覽摘要與概念 |
| 我的記憶 | `/agenthub/static/memory.html` | 查看／刪除 LLM 萃取的長期事實 |
| 個人設定 | `/agenthub/static/profile.html` | 設定語言、語氣、自訂指令 |

### 介面截圖

| 主畫面 | 對話介面 |
|--------|----------|
| ![main](agent_platform/static/screenshots/main.jpg) | ![talk](agent_platform/static/screenshots/talk.jpg) |

| Agent 編輯 | 對話歷史 | 個人設定 |
|------------|----------|----------|
| ![edit](agent_platform/static/screenshots/edit.jpg) | ![history](agent_platform/static/screenshots/history.jpg) | ![persona](agent_platform/static/screenshots/persona.jpg) |

---

## API 文件

啟動後至 `http://localhost:8000/docs` 查看完整 Swagger UI。

### 認證

```
POST /agenthub/auth/register    註冊（username、email、password）
POST /agenthub/auth/login       登入（email、password）
POST /agenthub/auth/google      Google OAuth 登入（credential）
GET  /agenthub/auth/me          取得當前使用者資訊
GET  /agenthub/auth/config      取得前端設定（google_client_id）
```

### Agent

```
POST   /agenthub/agents              建立 Agent
GET    /agenthub/agents              列出我的 Agent
GET    /agenthub/agents/{id}         取得 Agent 詳情
PUT    /agenthub/agents/{id}         更新 Agent
DELETE /agenthub/agents/{id}         刪除 Agent
GET    /agenthub/share/{slug}        公開 Agent 頁面（HTML）
POST   /agenthub/api/chat/{slug}     串流對話（SSE）
```

### 對話歷史

```
GET    /agenthub/api/conversations                    列出對話
GET    /agenthub/api/conversations/{id}/messages      取得訊息
DELETE /agenthub/api/conversations/{id}               刪除對話
```

### 個人設定

```
GET    /agenthub/api/profile    取得設定
PUT    /agenthub/api/profile    更新設定
DELETE /agenthub/api/profile    重設設定
```

### 記憶管理

```
GET    /agenthub/api/facts                       列出長期事實
DELETE /agenthub/api/facts/{id}                  刪除單筆事實
DELETE /agenthub/api/facts                       清除所有事實
GET    /agenthub/api/memory/session/{conv_id}    取得 session 記憶
GET    /agenthub/api/memory/long-term            取得長期記憶摘要
```

### 知識庫

```
POST   /agenthub/api/kb/documents           上傳文件
GET    /agenthub/api/kb/documents           列出文件
DELETE /agenthub/api/kb/documents/{id}      刪除文件
POST   /agenthub/api/kb/compile             手動觸發 LLM 編譯
GET    /agenthub/api/kb/summaries           列出摘要
GET    /agenthub/api/kb/concepts            列出概念條目
GET    /agenthub/api/kb/concepts/{name}     取得單一概念
GET    /agenthub/api/kb/search?q={keyword}  搜尋知識庫
```

### 媒體

```
POST /agenthub/api/upload/image    上傳圖片（multipart/form-data）
GET  /agenthub/api/imgproxy        圖片代理（解決外部圖片 CORS 問題）
```

---

## 目錄結構

```
agenthub/
├── agent_platform/
│   ├── app.py              FastAPI 主應用程式（所有 API endpoints）
│   ├── database.py         資料庫 schema 初始化與連線管理（含 migration）
│   ├── schemas.py          Pydantic 請求／回應資料模型
│   ├── auth.py             JWT 產生／驗證、密碼雜湊
│   ├── config.py           環境變數讀取
│   ├── llm_bridge.py       LLM 串流橋接層（Ollama / OpenAI / Azure，含 SSRF 防護）
│   ├── persona.py          長期 user facts 萃取（Tier 1）
│   ├── knowledge_base.py   知識庫編譯與 RAG 動態檢索（Tier 2–4）
│   ├── memory_manager.py   短期 session 工作記憶
│   ├── main.py             進入點（app 掛載於 /agenthub，PORT 可設定）
│   └── static/
│       ├── index.html      Agent 管理主介面
│       ├── chat.html       對話介面
│       ├── kb.html         知識庫管理介面
│       ├── memory.html     記憶管理介面
│       ├── profile.html    個人設定介面
│       └── screenshots/    介面截圖
├── test_e2e.py             端到端測試（完整 API 流程）
├── test_persona.py         Persona 單元測試
├── .env.example            環境變數範本（可安全 commit）
├── Dockerfile
├── zbpack.json             Zeabur 部署設定
└── README.md
```

---

## 資料庫結構

共 10 張資料表，全部由 `init_db()` 自動建立（含舊版資料庫的欄位 migration）：

| 資料表 | 說明 |
|--------|------|
| `users` | 使用者帳號（支援 Email 與 Google OAuth） |
| `agents` | Agent 設定（LLM 參數、系統提示、API key） |
| `conversations` | 對話紀錄（支援登入用戶與匿名 session） |
| `messages` | 訊息內容（含 token 計數） |
| `user_profiles` | 個人偏好設定（語言、語氣、自訂指令） |
| `user_facts` | 長期 user facts，Tier 1（key-value） |
| `session_memory` | 短期 session 工作記憶 |
| `kb_documents` | 知識庫原始文件，Tier 2 |
| `kb_summaries` | LLM 編譯摘要，Tier 3 |
| `kb_concepts` | 跨文件概念條目，Tier 4 |

---

## 支援的 LLM Provider

| Provider | `llm_provider` | `llm_base_url` 範例 | `llm_api_key` |
|----------|----------------|---------------------|---------------|
| Ollama（本機） | `ollama` | `http://localhost:11434` | 不需要 |
| OpenAI | `openai` | `https://api.openai.com/v1` | `sk-proj-...` |
| Azure OpenAI | `azure` | Azure endpoint URL | Azure API key |
| 相容 OpenAI API 的服務 | `openai` | 自訂 base_url | 對應 API key |

> LLM API key 儲存於 SQLite，每個 Agent 各自設定，不共用。

---

## 部署到 Zeabur

1. Fork 本 repo 並推送至 GitHub
2. 在 Zeabur 建立新服務，選擇 GitHub repo（會自動偵測 Dockerfile）
3. 在 **Variables** 頁面設定環境變數：

```
JWT_SECRET        = <執行下方指令產生>
GOOGLE_CLIENT_ID  = <Google Cloud Console 的 OAuth Client ID（選填）>
DB_PATH           = /data/agents.db
```

```bash
# 產生 JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"
```

4. 在 **Volumes** 頁面掛載 `/data` 目錄（確保資料在重部署後保留）

> 所有 key 只透過 Zeabur Variables 設定，不寫入程式碼或 git。

---

## 如何貢獻

1. Fork 本 repo
2. 建立 feature branch：`git checkout -b feature/my-feature`
3. 提交 PR，說明改動目的與測試方式

**歡迎貢獻的方向：**
- 向量語意搜尋（取代目前的關鍵詞 LIKE 搜尋）
- 更多 LLM provider（Gemini、Claude、Cohere）
- 多租戶 / 組織權限管理
- 測試覆蓋率提升

---

MIT License
