from typing import List, Optional, Any, Dict
from uuid import UUID
from pydantic import BaseModel, Field

# Auth Modelleri
class LoginRequest(BaseModel):
    username: str = Field(..., example="admin")
    password: str = Field(..., example="admin*123!")

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    department: str
    clearance_level: int

class UserContext(BaseModel):
    user_id: str
    email: str
    role: str
    department: str
    clearance_level: int

# Doküman Yönetim Modelleri
class DocumentResponse(BaseModel):
    id: str
    title: str
    document_code: Optional[str] = None
    file_hash: str
    department: str
    min_clearance_level: int
    version: int
    is_active: bool
    total_chunks: int

# RAG ve Chat Modelleri
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, example="Uzaktan çalışma politikası nasıldır?")
    lang: str = Field(default="tr", example="tr")
    session_id: Optional[str] = None

class ChunkCitation(BaseModel):
    id: str
    document_id: str
    document_title: Optional[str] = None
    document_code: Optional[str] = None
    page_number: Optional[int] = None
    content: str
    snippet: Optional[str] = None
    department: str
    min_clearance_level: int
    similarity: float

class QueryResponse(BaseModel):
    answer: str
    source_found: bool
    citations: List[ChunkCitation] = []
    session_id: Optional[str] = None
    execution_time_ms: int = 0

# Geri Bildirim ve Kürasyon Modelleri
class FeedbackRequest(BaseModel):
    query_text: str
    response_text: str
    feedback: int = Field(..., ge=-1, le=1) # 1: Faydalı, -1: Hatalı, 0: Nötr
    feedback_notes: Optional[str] = None
    corrected_answer: Optional[str] = None
    department: str = "genel"
    min_clearance_level: int = 10
    audit_log_id: Optional[str] = None

class FeedbackResponse(BaseModel):
    status: str
    message: str
    staging_id: Optional[str] = None

class CurationApproveRequest(BaseModel):
    staging_id: str
    verified_answer: Optional[str] = None

class CurationItemResponse(BaseModel):
    id: str
    original_query: str
    verified_answer: str
    department: str
    min_clearance_level: int
    status: str
    created_at: str

# Super Admin Yönetim Modelleri
class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, example="yeni_personel")
    password: str = Field(..., min_length=6, example="Pass*2026!")
    role_name: str = Field(default="user-genel", example="user-genel")
    department: str = Field(default="genel", example="ik")
    clearance_level: int = Field(default=10, example=10)

class AdminUserUpdateRequest(BaseModel):
    role_name: Optional[str] = None
    department: Optional[str] = None
    clearance_level: Optional[int] = None
    password: Optional[str] = None

class AdminUserResponse(BaseModel):
    user_id: str
    username: str
    role_name: str
    department: str
    clearance_level: int
    created_at: Optional[str] = None
    total_queries: int = 0
