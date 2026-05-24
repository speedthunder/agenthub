from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List


# ── Auth ──────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    email: EmailStr
    password: str = Field(..., min_length=8)  # L2 fix: 最低 8 字元


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleAuthRequest(BaseModel):
    credential: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str


# ── Agent ─────────────────────────────────────────────────
class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = ""
    avatar: Optional[str] = "🤖"
    system_prompt: str = "You are a helpful assistant."
    llm_provider: str = Field("ollama", pattern="^(ollama|openai|azure)$")
    llm_model: str = "llama3"
    llm_base_url: Optional[str] = "http://localhost:11434"
    llm_api_key: Optional[str] = ""
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=64, le=8192)
    is_public: bool = True
    welcome_message: Optional[str] = ""
    suggested_prompts: Optional[List[str]] = []
    cover_image_url: Optional[str] = ""
    theme_color: Optional[str] = "#6c63ff"
    skills: Optional[List[dict]] = []


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = None
    avatar: Optional[str] = None
    system_prompt: Optional[str] = None
    llm_provider: Optional[str] = Field(None, pattern="^(ollama|openai|azure)$")
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=64, le=8192)
    is_public: Optional[bool] = None
    welcome_message: Optional[str] = None
    suggested_prompts: Optional[List[str]] = None
    cover_image_url: Optional[str] = None
    theme_color: Optional[str] = None
    skills: Optional[List[dict]] = None


class AgentOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    avatar: str
    system_prompt: str
    llm_provider: str
    llm_model: str
    llm_base_url: str
    temperature: float
    max_tokens: int
    is_public: bool
    owner_id: int
    share_url: str
    welcome_message: str
    suggested_prompts: List[str]
    cover_image_url: str
    theme_color: str
    skills: List[dict]


# ── Chat ──────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    conversation_id: Optional[int] = None


# ── Conversation persistence ──────────────────────────────
class ConversationOut(BaseModel):
    id: int
    agent_id: int
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


# ── User profile (Phase 2) ────────────────────────────────
class ProfileOut(BaseModel):
    display_name: str = ""
    language: str = ""
    timezone: str = ""
    reply_style: str = ""        # '' | 'concise' | 'detailed'
    tone: str = ""               # '' | 'formal' | 'casual'
    use_emoji: int = -1          # -1 unset, 0 no, 1 yes
    interests: List[str] = []
    custom_instructions: str = ""
    share_with_agents: bool = True


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=80)
    language: Optional[str] = Field(None, max_length=20)
    timezone: Optional[str] = Field(None, max_length=40)
    reply_style: Optional[str] = Field(None, pattern="^(|concise|detailed)$")
    tone: Optional[str] = Field(None, pattern="^(|formal|casual)$")
    use_emoji: Optional[int] = Field(None, ge=-1, le=1)
    interests: Optional[List[str]] = None
    custom_instructions: Optional[str] = Field(None, max_length=2000)
    share_with_agents: Optional[bool] = None


# ── User facts (Phase 3) ──────────────────────────────────
class FactOut(BaseModel):
    id: int
    agent_id: Optional[int] = None
    category: str
    key: str
    value: str
    confidence: float
    created_at: str
    updated_at: str


# ── Knowledge Base ─────────────────────────────────────────
class KBDocumentCreate(BaseModel):
    title: str = Field("", max_length=200)
    content: str = Field(..., min_length=10, max_length=50000)
    source_type: str = Field("note", pattern="^(note|article|conversation)$")
    source_ref: str = ""
    tags: List[str] = []


class KBDocumentOut(BaseModel):
    id: int
    title: str
    content: str = ""
    source_type: str
    source_ref: str
    tags: List[str]
    compiled: bool
    created_at: str


class KBSummaryOut(BaseModel):
    id: int
    document_id: Optional[int] = None
    title: str
    origin: str
    core_conclusions: str
    key_evidence: str
    questions: str
    terms: str
    tags: List[str]
    created_at: str
    updated_at: str


class KBConceptOut(BaseModel):
    id: int
    name: str
    definition: str
    my_practice: str
    external_views: str
    tensions: str
    examples: str
    sources: List[int]
    source_count: int
    created_at: str
    updated_at: str


class KBCompileRequest(BaseModel):
    doc_ids: Optional[List[int]] = None      # None = compile all pending
    agent_id: Optional[int] = None           # which agent LLM to use; None = first agent


# ── Session Memory (short-term) ────────────────────────────
class SessionMemoryItem(BaseModel):
    key: str
    value: str


class SessionMemoryOut(BaseModel):
    conversation_id: int
    items: List[SessionMemoryItem]
