import os
import uuid
from typing import List, Optional
from contextlib import asynccontextmanager
import asyncpg
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.schemas.models import (
    LoginRequest, TokenResponse, UserContext, DocumentResponse,
    QueryRequest, QueryResponse, FeedbackRequest, FeedbackResponse,
    CurationApproveRequest, CurationItemResponse
)
from app.middleware.auth import (
    get_current_user, require_super_admin, require_admin,
    create_jwt_token, LOCAL_USER_METADATA
)
from app.services.ingestion import process_and_ingest_pdf
from app.services.rag_engine import execute_rag_query, stream_rag_query
from app.services.metrics import (
    record_audit_and_metrics, save_feedback_and_curation, approve_curation_item
)

# Global AsyncPG Database Pool
db_pool: Optional[asyncpg.Pool] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    print(f"[INFO] Veritabanı bağlantı havuzu başlatılıyor: {settings.DATABASE_URL.split('@')[-1]}...")
    try:
        db_pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
        print("[BAŞARILI] Veritabanı bağlantı havuzu aktif.")
    except Exception as e:
        print(f"[UYARI] Veritabanı havuzu başlatılamadı: {e}")
        db_pool = None
    yield
    if db_pool:
        await db_pool.close()
        print("[INFO] Veritabanı bağlantı havuzu kapatıldı.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Nirene AI Workspace & Enterprise RAG API - Multi-Tenant Knowledge Assistant",
    lifespan=lifespan
)

# CORS Yapılandırması
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if "*" not in cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    if not db_pool:
        raise HTTPException(status_code=503, detail="Veritabanı servisi hazır değil.")
    return db_pool

# -------------------------------------------------------------
# GENEL VE SAĞLIK KONTROLÜ ROTALARI
# -------------------------------------------------------------
@app.get("/", response_class=FileResponse)
def read_root():
    """Görsel Yapay Zeka Sohbet Arayüzü (Chat UI)."""
    index_path = "static/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Nirene AI Workspace running. Access /docs for Swagger UI."}

@app.get("/health")
async def healthcheck():
    """Docker Healthcheck ve Liveness izleme endpoint'i."""
    db_status = "connected" if db_pool else "disconnected"
    return {
        "status": "healthy",
        "service": "nirene-ai-workspace",
        "version": "2.0.0",
        "database": db_status
    }

# -------------------------------------------------------------
# KİMLİK DOĞRULAMA (AUTH) ROTALARI
# -------------------------------------------------------------
@app.post("/api/auth/login", response_model=TokenResponse)
@app.post("/api/login", response_model=TokenResponse) # Geriye dönük uyumluluk
def login(request: LoginRequest):
    """Kullanıcı adı ve şifre ile GoTrue uyumlu JWT Token üretir."""
    # Yerel kullanıcı ve şifre kontrolü
    passwords = {
        "admin": settings.ADMIN_PASSWORD,
        "staff": settings.STAFF_PASSWORD,
        "ik_admin": "ik*2026!",
        "hukuk_admin": "hukuk*2026!",
        "finans_admin": "finans*2026!"
    }
    expected_pass = passwords.get(request.username)
    if not expected_pass or expected_pass != request.password:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")

    meta = LOCAL_USER_METADATA.get(request.username, {
        "role": "user-genel",
        "department": "genel",
        "clearance_level": 10
    })
    token = create_jwt_token(request.username, meta)
    
    return TokenResponse(
        access_token=token,
        role=meta["role"],
        department=meta["department"],
        clearance_level=meta["clearance_level"]
    )

@app.get("/api/auth/me", response_model=UserContext)
def get_current_user_profile(user: UserContext = Depends(get_current_user)):
    """Mevcut giriş yapmış kullanıcının profil ve yetki bilgilerini döner."""
    return user

# -------------------------------------------------------------
# DOKÜMAN YÖNETİMİ VE INGESTION ROTALARI
# -------------------------------------------------------------
@app.post("/api/documents/upload", response_model=DocumentResponse)
async def upload_pdf_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    department: str = Form("genel"),
    min_clearance_level: int = Form(10),
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Super Admin yetkisiyle yeni PDF dokümanı yükler ve vektörleştirir."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Yalnızca PDF formatındaki dosyalar yüklenebilir.")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Yüklenen dosya boş olamaz.")

    result = await process_and_ingest_pdf(
        file_bytes=file_bytes,
        title=title.strip(),
        department=department.lower().strip(),
        min_clearance_level=min_clearance_level,
        uploaded_by=user.user_id,
        db_pool=pool
    )
    return DocumentResponse(**result)

@app.get("/api/documents", response_model=List[DocumentResponse])
async def list_documents(user: UserContext = Depends(get_current_user), pool = Depends(get_db)):
    """Kullanıcının görmeye yetkili olduğu aktif dokümanları listeler."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.title, d.file_hash, d.department, d.min_clearance_level, d.version, d.is_active,
                   COUNT(c.id) as total_chunks
            FROM documents d
            LEFT JOIN document_chunks c ON d.id = c.document_id AND c.is_active = TRUE
            WHERE d.is_active = TRUE
              AND ($1 = 'super_admin' OR (d.department = $2 OR d.department = 'genel') AND d.min_clearance_level <= $3)
            GROUP BY d.id
            ORDER BY d.created_at DESC
            """,
            user.role, user.department, user.clearance_level
        )
        return [
            DocumentResponse(
                id=str(r["id"]),
                title=r["title"],
                file_hash=r["file_hash"],
                department=r["department"],
                min_clearance_level=r["min_clearance_level"],
                version=r["version"],
                is_active=r["is_active"],
                total_chunks=r["total_chunks"]
            )
            for r in rows
        ]

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, user: UserContext = Depends(require_super_admin), pool = Depends(get_db)):
    """Dokümanı ve bağlı chunk'larını arşivler (Soft-Delete)."""
    clean_id = uuid.UUID(doc_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE documents SET is_active = FALSE WHERE id = $1", clean_id)
            await conn.execute("UPDATE document_chunks SET is_active = FALSE WHERE document_id = $1", clean_id)
    return {"status": "success", "message": f"Doküman ({doc_id}) başarıyla silindi/arşivlendi."}

# -------------------------------------------------------------
# RAG VE SOHBET ROTALARI
# -------------------------------------------------------------
@app.post("/api/chat/query", response_model=QueryResponse)
@app.post("/api/chat", response_model=QueryResponse) # Geriye dönük uyumluluk
async def chat_query_endpoint(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
    pool = Depends(get_db)
):
    """Kullanıcının rol ve yetkisine göre RLS korumalı RAG araması yapar ve yanıt üretir."""
    response = await execute_rag_query(
        user=user,
        query=request.question.strip(),
        lang=request.lang or "tr",
        session_id=request.session_id,
        db_pool=pool
    )

    # Denetim logu ve metrik kaydını asenkron olarak arka plana at
    chunk_ids = [c.id for c in response.citations]
    background_tasks.add_task(
        record_audit_and_metrics,
        user=user,
        session_id=request.session_id,
        query_text=request.question.strip(),
        retrieved_chunk_ids=chunk_ids,
        response_text=response.answer,
        execution_time_ms=response.execution_time_ms,
        prompt_tokens=len(request.question) // 4,
        completion_tokens=len(response.answer) // 4,
        db_pool=pool
    )
    return response

@app.get("/api/chat/stream")
async def chat_stream_endpoint(
    question: str = Query(..., min_length=1),
    lang: str = Query("tr"),
    session_id: Optional[str] = Query(None),
    user: UserContext = Depends(get_current_user),
    pool = Depends(get_db)
):
    """Server-Sent Events (SSE) streaming formatında yanıt iletir."""
    return StreamingResponse(
        stream_rag_query(user, question.strip(), lang, session_id, pool),
        media_type="text/event-stream"
    )

# -------------------------------------------------------------
# GERİ BİLDİRİM VE KURUMSAL BİLGİ KÜRASYONU (FLYWHEEL)
# -------------------------------------------------------------
@app.post("/api/chat/feedback", response_model=FeedbackResponse)
async def post_feedback(
    request: FeedbackRequest,
    user: UserContext = Depends(get_current_user),
    pool = Depends(get_db)
):
    """Kullanıcının chat cevabına verdiği puanı kaydeder ve kürasyon havuzuna ekler."""
    staging_id = await save_feedback_and_curation(
        user=user,
        audit_log_id=request.audit_log_id,
        query_text=request.query_text,
        response_text=request.response_text,
        feedback=request.feedback,
        feedback_notes=request.feedback_notes,
        corrected_answer=request.corrected_answer,
        department=request.department or user.department,
        min_clearance_level=request.min_clearance_level,
        db_pool=pool
    )
    return FeedbackResponse(
        status="success",
        message="Geri bildirim başarıyla kaydedildi.",
        staging_id=staging_id
    )

@app.get("/api/curation/pending", response_model=List[CurationItemResponse])
async def get_pending_curations(
    user: UserContext = Depends(require_admin),
    pool = Depends(get_db)
):
    """Adminler için onay bekleyen kullanıcı geri bildirimlerini listeler."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, original_query, verified_answer, department, min_clearance_level, status, created_at
            FROM knowledge_staging
            WHERE status = 'pending'
              AND ($1 = 'super_admin' OR department = $2 OR department = 'genel')
            ORDER BY created_at DESC
            """,
            user.role, user.department
        )
        return [
            CurationItemResponse(
                id=str(r["id"]),
                original_query=r["original_query"],
                verified_answer=r["verified_answer"],
                department=r["department"],
                min_clearance_level=r["min_clearance_level"],
                status=r["status"],
                created_at=str(r["created_at"])
            )
            for r in rows
        ]

@app.post("/api/curation/approve")
async def approve_curation(
    request: CurationApproveRequest,
    user: UserContext = Depends(require_admin),
    pool = Depends(get_db)
):
    """Kürasyon havuzundaki soru-cevap öğesini onaylar ve kalıcı vektör olarak kaydeder."""
    result = await approve_curation_item(
        staging_id=request.staging_id,
        verified_answer=request.verified_answer,
        approved_by=user.user_id,
        db_pool=pool
    )
    return {"status": "success", "data": result}
