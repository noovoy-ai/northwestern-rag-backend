import os
import requests
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from jose import jwt, JWTError
from ingest import rebuild_vector_db

# Northwestern Staff Handbook RAG API - Staging Environment
app = FastAPI(title="Northwestern Staff Handbook RAG API")

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
        collection_name=COLLECTION_NAME
    )

vector_store = get_vector_store()

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

@app.post("/api/chat")
def chat_endpoint(request: QueryRequest, user_data: dict = Depends(verify_jwt_token)):
    user_query = request.question.strip()
    
    if not user_query:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    search_query = f"search_query: {user_query}"
    results = vector_store.similarity_search_with_score(search_query, k=5)
    
    COSINE_THRESHOLD = 0.65
    filtered_results = [doc for doc, score in results if score <= COSINE_THRESHOLD]

    if not filtered_results:
        return {
            "answer": "This information is not available in the staff handbook.",
            "source_found": False,
            "section": None
        }

    context_blocks = [doc.page_content.replace("search_document: ", "") for doc in filtered_results]
    context_text = "\n\n---\n\n".join(context_blocks)
    first_doc_meta = filtered_results[0].metadata
    matched_section = first_doc_meta.get("subsection_title") or first_doc_meta.get("section_title") or "General"

    system_prompt = f"""You are the Northwestern University Staff Handbook Assistant.
Below is the 'CONTEXT TEXT' extracted from the official staff handbook.

STRICT RULES TO FOLLOW:
1. Answer the user's question based ONLY on the provided 'CONTEXT TEXT'.
2. Do NOT use any outside knowledge, assumptions, or extrapolations not present in the context text.
3. If the answer is NOT explicitly stated in the context text, respond ONLY with: "This information is not available in the staff handbook."
4. Provide a clear, professional, concise, and grammatically correct response in English.

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
        response = requests.post(ollama_generate_url, json=payload, timeout=60)
        bot_response = response.json().get("response", "No response received.")
        
        return {
            "answer": bot_response,
            "source_found": True,
            "section": matched_section
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM connection error ({OLLAMA_BASE_URL}): {str(e)}")
