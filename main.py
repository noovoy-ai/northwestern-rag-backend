import os
import requests
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from jose import jwt, JWTError
from ingest import rebuild_vector_db

# Northwestern Staff Handbook RAG API - Staging Environment
app = FastAPI(title="Northwestern Staff Handbook RAG API")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# Ortam Değişkenleri Yapılandırması
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:7b")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
DB_DIR = os.getenv("DB_DIR", "./chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "staff_handbook")
SECRET_KEY = os.getenv("SECRET_KEY", "1905HuQ?0201..Zx*")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "*")

cors_origins = [origin.strip() for origin in CORS_ORIGINS_RAW.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if "*" not in cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# Kullanıcı Veritabanı (Ortam Değişkenlerinden Okuma)
USERS_DB = {
    "admin": os.getenv("ADMIN_PASSWORD", "admin*123!"),
    "staff": os.getenv("STAFF_PASSWORD", "nu2026pass")
}

class LoginRequest(BaseModel):
    username: str
    password: str

class QueryRequest(BaseModel):
    question: str
    lang: str = "tr"  # "tr" or "en"

def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token.")

def get_vector_store():
    embeddings = OllamaEmbeddings(
        model=EMBEDDING_MODEL_NAME,
        base_url=OLLAMA_BASE_URL
    )
    return Chroma(
        persist_directory=DB_DIR,
        embedding_function=embeddings,
        collection_name=COLLECTION_NAME,
        collection_metadata={"hnsw:space": "cosine"}
    )

vector_store = get_vector_store()

@app.get("/", response_class=FileResponse)
def read_root():
    """Görsel Yapay Zeka Sohbet Arayüzü (Chat UI)."""
    index_path = "static/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Northwestern Staff Handbook RAG API running. Access /docs for Swagger UI."}

@app.get("/health")
def healthcheck():
    """Docker Healthcheck ve Liveness izleme endpoint'i."""
    return {"status": "healthy", "service": "northwestern-rag-backend"}

@app.post("/api/login")
def login_endpoint(request: LoginRequest):
    stored_password = USERS_DB.get(request.username)
    if not stored_password or stored_password != request.password:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")
    
    payload = {"sub": request.username, "role": "admin" if request.username == "admin" else "user"}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/admin/ingest")
def admin_ingest_endpoint(user_data: dict = Depends(verify_jwt_token)):
    """Sadece Admin yetkisine sahip kullanıcıların vektör veritabanını güncelleyebileceği endpoint."""
    if user_data.get("sub") != "admin":
        raise HTTPException(status_code=403, detail="Bu işlem için Admin yetkisi gereklidir.")
    
    global vector_store
    try:
        if vector_store and hasattr(vector_store, "_client"):
            try:
                vector_store._client.close()
            except Exception:
                pass
        
        total_chunks = rebuild_vector_db()
        vector_store = get_vector_store()
        return {
            "status": "success",
            "message": "Vektör veritabanı başarıyla yeniden oluşturuldu.",
            "total_chunks": total_chunks
        }
    except Exception as e:
        vector_store = get_vector_store()
        raise HTTPException(status_code=500, detail=f"Ingestion sırasında hata oluştu: {str(e)}")

def detect_language(text: str, user_lang: str) -> str:
    """Detect language based on user preference or text content."""
    if user_lang in ["tr", "en"]:
        return user_lang
    turkish_chars = set("çğıöşüÇĞİÖŞÜ")
    if any(c in turkish_chars for c in text):
        return "tr"
    return "tr"

TR_KEYWORD_MAP = {
    "tatil": "vacation holiday leave accrual",
    "izin": "leave absence vacation PTO sick time",
    "uzaktan": "remote work flexible working arrangement",
    "çalışma": "work employment schedule hours",
    "sağlık": "health medical insurance benefits plan",
    "sigorta": "insurance health benefits coverage",
    "maaş": "salary pay compensation wages",
    "ücret": "compensation pay salary wages",
    "disiplin": "discipline conduct policy standards",
    "istifa": "resignation termination separation",
    "emeklilik": "retirement pension benefits",
    "bayram": "holiday university official holidays",
}

def expand_search_query(query: str, user_lang: str) -> str:
    if user_lang == "tr":
        query_lower = query.lower()
        matched = [eng for tr, eng in TR_KEYWORD_MAP.items() if tr in query_lower]
        if matched:
            return f"{query} {' '.join(matched)}"
    return query

@app.post("/api/chat")
def chat_endpoint(request: QueryRequest, user_data: dict = Depends(verify_jwt_token)):
    user_query = request.question.strip()
    user_lang = detect_language(user_query, request.lang.strip().lower())
    
    if not user_query:
        raise HTTPException(status_code=400, detail="Question cannot be empty." if user_lang == "en" else "Soru boş olamaz.")

    # Vector search with expanded search query for high recall
    search_query = expand_search_query(user_query, user_lang)
    results = vector_store.similarity_search_with_score(search_query, k=7)

    
    COSINE_THRESHOLD = 0.72 if user_lang == "tr" else 0.65
    filtered_results = [doc for doc, score in results if score <= COSINE_THRESHOLD]

    if not filtered_results:
        fallback_msg = (
            "Bu bilgi personel el kitabında yer almamaktadır."
            if user_lang == "tr"
            else "This information is not available in the staff handbook."
        )
        return {
            "answer": fallback_msg,
            "source_found": False,
            "section": None
        }

    context_blocks = [doc.page_content for doc in filtered_results]
    context_text = "\n\n---\n\n".join(context_blocks)
    first_doc_meta = filtered_results[0].metadata
    matched_section = first_doc_meta.get("subsection_title") or first_doc_meta.get("section_title") or "Genel / General"

    if user_lang == "tr":
        system_prompt = f"""You are the official Northwestern University Staff Handbook AI Assistant.
Below is the 'CONTEXT TEXT' extracted from the official Northwestern Staff Handbook (written in English).

STRICT RULES TO FOLLOW:
1. The user asked the question in TURKISH. Read the provided English 'CONTEXT TEXT' and synthesize a comprehensive, accurate, and fluent answer in TURKISH (Türkçe).
2. Base your response strictly and ONLY on the facts and guidelines given in the 'CONTEXT TEXT'.
3. Do NOT invent outside information not supported by the context text.
4. ONLY if the context text contains no relevant information regarding the user's question topic, respond strictly with: "Bu bilgi personel el kitabında yer almamaktadır."
5. Structure your Turkish response nicely using clear markdown formatting (bullet points, headings).

CONTEXT TEXT:
{context_text}

USER QUESTION (IN TURKISH):
{user_query}
"""
    else:
        system_prompt = f"""You are the official Northwestern University Staff Handbook AI Assistant.
Below is the 'CONTEXT TEXT' extracted from the official Northwestern Staff Handbook.

STRICT RULES TO FOLLOW:
1. Answer the user's question accurately and thoroughly based ONLY on the provided 'CONTEXT TEXT'.
2. Do NOT use outside knowledge, assumptions, or unverified claims not stated in the context text.
3. If the answer is NOT present in the provided context text, respond strictly with: "This information is not available in the staff handbook."
4. Provide a clear, helpful, professional, and well-structured response in English.

CONTEXT TEXT:
{context_text}

USER QUESTION:
{user_query}
"""

    ollama_generate_url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate"
    payload = {
        "model": MODEL_NAME,
        "prompt": system_prompt,
        "stream": False
    }

    try:
        response = requests.post(ollama_generate_url, json=payload, timeout=180)
        bot_response = response.json().get("response", "No response received.")
        
        return {
            "answer": bot_response,
            "source_found": True,
            "section": matched_section
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM connection error ({OLLAMA_BASE_URL}): {str(e)}")
