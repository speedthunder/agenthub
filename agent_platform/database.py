import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "agents.db")


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT UNIQUE NOT NULL,
                email     TEXT UNIQUE NOT NULL,
                hashed_pw TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agents (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                slug         TEXT UNIQUE NOT NULL,
                name         TEXT NOT NULL,
                description  TEXT DEFAULT '',
                avatar       TEXT DEFAULT '🤖',
                system_prompt TEXT NOT NULL DEFAULT 'You are a helpful assistant.',
                llm_provider TEXT NOT NULL DEFAULT 'ollama',
                llm_model    TEXT NOT NULL DEFAULT 'llama3',
                llm_base_url TEXT DEFAULT 'http://localhost:11434',
                llm_api_key  TEXT DEFAULT '',
                temperature  REAL DEFAULT 0.7,
                max_tokens   INTEGER DEFAULT 1024,
                is_public    INTEGER DEFAULT 1,
                welcome_message   TEXT DEFAULT '',
                suggested_prompts TEXT DEFAULT '[]',
                cover_image_url   TEXT DEFAULT '',
                theme_color       TEXT DEFAULT '#6c63ff',
                skills            TEXT DEFAULT '[]',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
                anon_session_id TEXT,
                title           TEXT DEFAULT '',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user_agent
                ON conversations(user_id, agent_id);
            CREATE INDEX IF NOT EXISTS idx_conv_anon_agent
                ON conversations(anon_session_id, agent_id);

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                token_count     INTEGER,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv
                ON messages(conversation_id, id);

            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                display_name        TEXT DEFAULT '',
                language            TEXT DEFAULT '',
                timezone            TEXT DEFAULT '',
                reply_style         TEXT DEFAULT '',     -- 'concise' | 'detailed' | ''
                tone                TEXT DEFAULT '',     -- 'formal' | 'casual' | ''
                use_emoji           INTEGER DEFAULT -1,  -- -1 unset, 0 no, 1 yes
                interests           TEXT DEFAULT '[]',   -- JSON array
                custom_instructions TEXT DEFAULT '',
                share_with_agents   INTEGER DEFAULT 1,   -- 0 = 禁止注入給任何 agent
                updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_facts (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                agent_id           INTEGER REFERENCES agents(id) ON DELETE CASCADE,
                category           TEXT NOT NULL,           -- work | family | preference | event | misc ...
                key                TEXT NOT NULL,
                value              TEXT NOT NULL,
                confidence         REAL DEFAULT 0.8,
                source_message_id  INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, agent_id, category, key)
            );
            CREATE INDEX IF NOT EXISTS idx_facts_user
                ON user_facts(user_id, agent_id);

            -- Short-term: working memory for the current conversation session
            CREATE TABLE IF NOT EXISTS session_memory (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                key             TEXT NOT NULL,
                value           TEXT NOT NULL,
                updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(conversation_id, key)
            );
            CREATE INDEX IF NOT EXISTS idx_session_mem
                ON session_memory(conversation_id);

            -- Long-term KB: raw documents uploaded by user
            CREATE TABLE IF NOT EXISTS kb_documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title       TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL,
                source_type TEXT DEFAULT 'note',       -- note | article | conversation
                source_ref  TEXT DEFAULT '',            -- conversation_id or URL
                tags        TEXT DEFAULT '[]',
                compiled    INTEGER DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_kb_docs_user
                ON kb_documents(user_id);

            -- Long-term KB: compiled summaries (wiki/summaries equivalent)
            CREATE TABLE IF NOT EXISTS kb_summaries (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                document_id      INTEGER REFERENCES kb_documents(id) ON DELETE SET NULL,
                title            TEXT NOT NULL DEFAULT '',
                origin           TEXT DEFAULT 'note',   -- note | article | conversation
                core_conclusions TEXT DEFAULT '',
                key_evidence     TEXT DEFAULT '',
                questions        TEXT DEFAULT '',
                terms            TEXT DEFAULT '',
                tags             TEXT DEFAULT '[]',
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_kb_summaries_user
                ON kb_summaries(user_id);

            -- Long-term KB: concept entries across conversations (wiki/concepts equivalent)
            CREATE TABLE IF NOT EXISTS kb_concepts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name           TEXT NOT NULL,
                definition     TEXT NOT NULL DEFAULT '',
                my_practice    TEXT DEFAULT '',
                external_views TEXT DEFAULT '',
                tensions       TEXT DEFAULT '',
                examples       TEXT DEFAULT '',
                sources        TEXT DEFAULT '[]',       -- JSON array of summary ids
                source_count   INTEGER DEFAULT 0,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_kb_concepts_user
                ON kb_concepts(user_id);
        """)
        # 加 google_id 欄位（舊 DB 相容，已存在就跳過）
        try:
            conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
        except Exception:
            pass


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
