import os
import uuid
import json
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
    CurationApproveRequest, CurationItemResponse,
    AdminUserCreateRequest, AdminUserUpdateRequest, AdminUserResponse
)
from app.middleware.auth import (
    get_current_user, require_super_admin, require_admin,
    create_jwt_token, get_password_hash, verify_password, LOCAL_USER_METADATA
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
        
        # Default kullanıcıları güvenli parola hash'leriyle user_profiles tablosuna tohumla (seed)
        async with db_pool.acquire() as conn:
            default_users = [
                ("admin", settings.ADMIN_PASSWORD, "super_admin", "genel", 100, "admin@northwestern.edu"),
                ("staff", settings.STAFF_PASSWORD, "user-genel", "genel", 10, "staff@northwestern.edu"),
                ("ik_admin", "ik*2026!", "admin-ik", "ik", 50, "ik_admin@northwestern.edu"),
                ("hukuk_admin", "hukuk*2026!", "admin-hukuk", "hukuk", 50, "hukuk_admin@northwestern.edu"),
                ("finans_admin", "finans*2026!", "admin-finans", "finans", 50, "finans_admin@northwestern.edu"),
            ]
            for uname, plain_pw, role, dept, clearance, email in default_users:
                uid = uuid.uuid5(uuid.NAMESPACE_DNS, uname)
                pw_hash = get_password_hash(plain_pw)
                await conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, username, email, password_hash, role_name, department, clearance_level)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id) DO UPDATE 
                    SET username = EXCLUDED.username,
                        password_hash = EXCLUDED.password_hash,
                        role_name = EXCLUDED.role_name,
                        department = EXCLUDED.department,
                        clearance_level = EXCLUDED.clearance_level;
                    """,
                    uid, uname, email, pw_hash, role, dept, clearance
                )
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
async def login(request: LoginRequest, pool = Depends(get_db)):
    """Kullanıcı adı ve şifre ile veritabanından güvenli PBKDF2 hash doğrulaması yaparak JWT üretir."""
    uname = request.username.strip()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, username, email, password_hash, role_name, department, clearance_level FROM user_profiles WHERE username = $1",
            uname
        )
    
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")
        
    if not verify_password(request.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")
        
    meta = {
        "role": row["role_name"],
        "department": row["department"],
        "clearance_level": row["clearance_level"],
        "email": row["email"]
    }
    token = create_jwt_token(row["username"], meta)
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
    document_code: Optional[str] = Form(None),
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
        db_pool=pool,
        document_code=document_code.strip() if document_code else None
    )
    return DocumentResponse(**result)

@app.get("/api/documents", response_model=List[DocumentResponse])
async def list_documents(user: UserContext = Depends(get_current_user), pool = Depends(get_db)):
    """Kullanıcının görmeye yetkili olduğu aktif dokümanları listeler."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.title, d.document_code, d.file_hash, d.department, d.min_clearance_level, d.version, d.is_active,
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
                document_code=r["document_code"],
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

# -------------------------------------------------------------
# SOHBET GEÇMİŞİ VE OTURUM YÖNETİMİ ROTALARI
# -------------------------------------------------------------
@app.get("/api/chat/sessions")
async def get_user_chat_sessions(
    user: UserContext = Depends(get_current_user),
    pool = Depends(get_db)
):
    """Kullanıcının geçmiş sohbet oturumlarını listeler."""
    clean_uid = uuid.UUID(user.user_id) if isinstance(user.user_id, str) else user.user_id
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id,
                   MIN(query_text) as first_query,
                   MAX(created_at) as last_activity,
                   COUNT(id) as total_messages
            FROM audit_logs
            WHERE user_id = $1 AND session_id IS NOT NULL
            GROUP BY session_id
            ORDER BY last_activity DESC
            LIMIT 30;
            """,
            clean_uid
        )
        return [
            {
                "session_id": str(r["session_id"]),
                "title": r["first_query"][:50] + ("..." if len(r["first_query"]) > 50 else ""),
                "last_activity": str(r["last_activity"]),
                "total_messages": r["total_messages"]
            }
            for r in rows
        ]

@app.get("/api/chat/history/{session_id}")
async def get_chat_session_history(
    session_id: str,
    user: UserContext = Depends(get_current_user),
    pool = Depends(get_db)
):
    """Belirli bir oturuma ait geçmiş mesajları ve cevapları getirir."""
    try:
        clean_sid = uuid.UUID(session_id)
        clean_uid = uuid.UUID(user.user_id) if isinstance(user.user_id, str) else user.user_id
    except ValueError:
        raise HTTPException(status_code=400, detail="Geçersiz session_id formatı.")
        
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, query_text, response_text, user_feedback, created_at
            FROM audit_logs
            WHERE session_id = $1 AND user_id = $2
            ORDER BY created_at ASC
            """,
            clean_sid, clean_uid
        )
        return [
            {
                "id": str(r["id"]),
                "query_text": r["query_text"],
                "response_text": r["response_text"],
                "feedback": r["user_feedback"],
                "created_at": str(r["created_at"])
            }
            for r in rows
        ]

# -------------------------------------------------------------
# SUPER ADMIN 2 AŞAMALI MULTI-AGENT & DASHBOARD ROTALARI
# -------------------------------------------------------------
@app.get("/api/admin/fleet-overview")
async def get_fleet_overview(user: UserContext = Depends(require_super_admin), pool = Depends(get_db)):
    """Tüm yapay zeka ajan filosunun durumunu ve metriklerini döner (Aşama 1)."""
    async with pool.acquire() as conn:
        doc_count = await conn.fetchval("SELECT COUNT(*) FROM documents WHERE is_active = TRUE") or 0
        query_count = await conn.fetchval("SELECT COUNT(*) FROM audit_logs") or 0
        user_count = await conn.fetchval("SELECT COUNT(*) FROM user_profiles") or 0
        
    fleet = [
        {
            "agent_id": "onboarding-nirene",
            "agent_name": "Nirene (Onboarding Agent)",
            "code": "agent_onboarding",
            "icon": "onboarding",
            "is_active": True,
            "status": "active",
            "status_label": "Aktif & Canlı (MVP)",
            "model": settings.LLM_MODEL,
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "Yeni işe başlayan personelin şirket kültürüne, izin ve çalışma prosedürlerine, donanım ve departman onay süreçlerine adaptasyonunu sağlayan ana kurumsal onboarding asistanı.",
            "stats": {
                "total_docs": doc_count,
                "total_queries": query_count,
                "authorized_users": user_count
            }
        },
        {
            "agent_id": "wellness-agent",
            "agent_name": "Wellness Agent",
            "code": "agent_wellness",
            "icon": "heart",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:7b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "hr-intelligence-agent",
            "agent_name": "HR Intelligence Agent",
            "code": "agent_hr_intelligence",
            "icon": "brain",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:14b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "manager-agent",
            "agent_name": "Manager Agent",
            "code": "agent_manager",
            "icon": "user-check",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:14b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "culture-agent",
            "agent_name": "Culture Agent",
            "code": "agent_culture",
            "icon": "sparkles",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:7b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "learning-agent",
            "agent_name": "Learning Agent",
            "code": "agent_learning",
            "icon": "book-open",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:7b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "crisis-agent",
            "agent_name": "Crisis Agent",
            "code": "agent_crisis",
            "icon": "alert-triangle",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:14b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        },
        {
            "agent_id": "retention-agent",
            "agent_name": "Retention Agent",
            "code": "agent_retention",
            "icon": "shield-check",
            "is_active": False,
            "status": "soon",
            "status_label": "Yakında Gelecek",
            "model": "qwen2.5:7b (Planlanan)",
            "embedding_model": settings.EMBEDDING_MODEL,
            "description": "",
            "stats": {
                "total_docs": 0,
                "total_queries": 0,
                "authorized_users": 0
            }
        }
    ]
    return {
        "total_fleet_count": len(fleet),
        "active_count": 1,
        "system_status": "healthy",
        "fleet": fleet
    }

@app.get("/api/admin/metrics")
async def get_admin_metrics(user: UserContext = Depends(require_super_admin), pool = Depends(get_db)):
    """Super Admin için detaylı sistem ve Nirene performans KPI'larını döner."""
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM user_profiles") or 0
        total_docs = await conn.fetchval("SELECT COUNT(*) FROM documents WHERE is_active = TRUE") or 0
        total_chunks = await conn.fetchval("SELECT COUNT(*) FROM document_chunks WHERE is_active = TRUE") or 0
        total_queries = await conn.fetchval("SELECT COUNT(*) FROM audit_logs") or 0
        
        avg_latency = await conn.fetchval("SELECT COALESCE(AVG(execution_time_ms), 0) FROM audit_logs") or 0
        pos_feedback = await conn.fetchval("SELECT COUNT(*) FROM audit_logs WHERE user_feedback = 1") or 0
        neg_feedback = await conn.fetchval("SELECT COUNT(*) FROM audit_logs WHERE user_feedback = -1") or 0
        pending_curation = await conn.fetchval("SELECT COUNT(*) FROM knowledge_staging WHERE status = 'pending'") or 0
        
        # Departman kullanıcı dağılımı
        dept_rows = await conn.fetch("SELECT department, COUNT(*) as count FROM user_profiles GROUP BY department")
        dept_dist = {r["department"]: r["count"] for r in dept_rows}
        
        # Departman sorgu dağılımı
        dept_query_rows = await conn.fetch(
            """
            SELECT COALESCE(u.department, 'genel') as department, COUNT(a.id) as query_count
            FROM audit_logs a
            LEFT JOIN user_profiles u ON a.user_id = u.user_id
            GROUP BY COALESCE(u.department, 'genel')
            """
        )
        dept_query_dist = {r["department"]: r["query_count"] for r in dept_query_rows}
        
        # Son 24 saat aktif kullanıcı sayısı
        active_24h = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM audit_logs WHERE created_at >= NOW() - INTERVAL '24 HOURS'") or 0
        
        satisfaction_rate = 100.0 if (pos_feedback + neg_feedback) == 0 else round((pos_feedback / (pos_feedback + neg_feedback)) * 100, 1)
        
        return {
            "total_users": total_users,
            "active_users_24h": active_24h,
            "total_docs": total_docs,
            "total_chunks": total_chunks,
            "total_queries": total_queries,
            "avg_latency_ms": int(avg_latency),
            "pos_feedback": pos_feedback,
            "neg_feedback": neg_feedback,
            "satisfaction_rate": satisfaction_rate,
            "pending_curation": pending_curation,
            "dept_distribution": dept_dist,
            "dept_query_distribution": dept_query_dist,
            "model_name": settings.LLM_MODEL,
            "embedding_model": settings.EMBEDDING_MODEL
        }

@app.get("/api/admin/users", response_model=List[AdminUserResponse])
async def list_admin_users(user: UserContext = Depends(require_super_admin), pool = Depends(get_db)):
    """Kayıtlı tüm kullanıcıları ve kullanım metriklerini listeler."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, username, role_name, department, clearance_level, created_at,
                   COALESCE(total_queries, 0) as total_queries
            FROM user_profiles
            ORDER BY clearance_level DESC, created_at ASC
            """
        )
        return [
            AdminUserResponse(
                user_id=str(r["user_id"]),
                username=r["username"],
                role_name=r["role_name"],
                department=r["department"],
                clearance_level=r["clearance_level"],
                created_at=str(r["created_at"]) if r["created_at"] else None,
                total_queries=r["total_queries"]
            )
            for r in rows
        ]

@app.post("/api/admin/users")
async def create_admin_user(
    req: AdminUserCreateRequest,
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Yeni bir kullanıcı tanımlar ve parolasını PBKDF2 ile hash'ler."""
    async with pool.acquire() as conn:
        clean_user = req.username.strip().lower()
        existing = await conn.fetchval("SELECT username FROM user_profiles WHERE username = $1", clean_user)
        if existing:
            raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten mevcut.")
        
        pwd_hash = get_password_hash(req.password)
        new_uid = uuid.uuid4()
        user_email = f"{clean_user}@northwestern.edu"
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, username, email, password_hash, role_name, department, clearance_level)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            new_uid, clean_user, user_email, pwd_hash, req.role_name, req.department.lower(), req.clearance_level
        )
        return {"status": "success", "message": f"Kullanıcı '{clean_user}' başarıyla oluşturuldu.", "user_id": str(new_uid)}

@app.put("/api/admin/users/{user_id}")
async def update_admin_user(
    user_id: str,
    req: AdminUserUpdateRequest,
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Kullanıcının rolünü, departmanını, yetki seviyesini veya şifresini günceller."""
    clean_uid = uuid.UUID(user_id)
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT username, role_name FROM user_profiles WHERE user_id = $1", clean_uid)
        if not target:
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
        
        if req.password:
            new_hash = get_password_hash(req.password)
            await conn.execute("UPDATE user_profiles SET password_hash = $1 WHERE user_id = $2", new_hash, clean_uid)
        if req.role_name is not None:
            await conn.execute("UPDATE user_profiles SET role_name = $1 WHERE user_id = $2", req.role_name, clean_uid)
        if req.department is not None:
            await conn.execute("UPDATE user_profiles SET department = $1 WHERE user_id = $2", req.department.lower(), clean_uid)
        if req.clearance_level is not None:
            await conn.execute("UPDATE user_profiles SET clearance_level = $1 WHERE user_id = $2", req.clearance_level, clean_uid)
            
        return {"status": "success", "message": f"Kullanıcı '{target['username']}' güncellendi."}

@app.delete("/api/admin/users/{user_id}")
async def delete_admin_user(
    user_id: str,
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Kullanıcı hesabını siler (Ana admin silinemez)."""
    clean_uid = uuid.UUID(user_id)
    async with pool.acquire() as conn:
        target = await conn.fetchrow("SELECT username FROM user_profiles WHERE user_id = $1", clean_uid)
        if not target:
            raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
        if target["username"] == "admin":
            raise HTTPException(status_code=400, detail="Ana 'admin' hesabı silinemez.")
        await conn.execute("DELETE FROM user_profiles WHERE user_id = $1", clean_uid)
        return {"status": "success", "message": f"Kullanıcı '{target['username']}' silindi."}

@app.get("/api/admin/audit-logs")
async def get_admin_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Tüm sistem denetim izini ve sorgu loglarını getirir."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id, a.user_id, u.username, a.session_id, a.query_text, a.response_text,
                   a.execution_time_ms, a.user_feedback, a.created_at
            FROM audit_logs a
            LEFT JOIN user_profiles u ON a.user_id = u.user_id
            ORDER BY a.created_at DESC
            LIMIT $1
            """,
            limit
        )
        return [
            {
                "id": str(r["id"]),
                "username": r["username"] or "Anonim/Silinmiş",
                "session_id": str(r["session_id"]) if r["session_id"] else None,
                "query_text": r["query_text"],
                "response_text": r["response_text"],
                "execution_time_ms": r["execution_time_ms"],
                "feedback": r["user_feedback"],
                "created_at": str(r["created_at"])
            }
            for r in rows
        ]

@app.get("/api/admin/documents/{doc_id}/chunks")
async def get_admin_document_chunks(
    doc_id: str,
    user: UserContext = Depends(require_super_admin),
    pool = Depends(get_db)
):
    """Belirli bir dokümanın tüm vektör parçalarını (chunks) listeler."""
    clean_id = uuid.UUID(doc_id)
    async with pool.acquire() as conn:
        doc = await conn.fetchrow("SELECT title, document_code FROM documents WHERE id = $1", clean_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Doküman bulunamadı.")
            
        rows = await conn.fetch(
            """
            SELECT id, chunk_index, content, metadata, min_clearance_level, is_active
            FROM document_chunks
            WHERE document_id = $1
            ORDER BY chunk_index ASC
            """,
            clean_id
        )
        return {
            "document_id": doc_id,
            "title": doc["title"],
            "document_code": doc["document_code"],
            "total_chunks": len(rows),
            "chunks": [
                {
                    "id": str(r["id"]),
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "snippet": r["content"][:200] + ("..." if len(r["content"]) > 200 else ""),
                    "metadata": json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {}),
                    "min_clearance_level": r["min_clearance_level"],
                    "is_active": r["is_active"]
                }
                for r in rows
            ]
        }
