import os
import re
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

vector_store = None

def get_db():
    global vector_store
    if vector_store is None:
        vector_store = get_vector_store()
    return vector_store


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

import re

def contains_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def clean_markdown_wrappers(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'^```(?:markdown)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    text = re.sub(r'^markdown\s*\n', '', text, flags=re.IGNORECASE)
    return text.strip()

def detect_language(text: str, user_lang: str) -> str:
    """Detect language based on user preference or text content."""
    if user_lang in ["tr", "en"]:
        return user_lang
    turkish_chars = set("çğıöşüÇĞİÖŞÜ")
    if any(c in turkish_chars for c in text):
        return "tr"
    tr_words = {"merhaba", "nasılsın", "nasıl", "selam", "günaydın", "iyi", "bugün", "hastayım", "izin", "nedir", "veya", "var", "yok", "hakkında", "bilgi", "gideceğim", "gidemice", "yapmalıyım", "işe"}
    words = set(re.findall(r'\w+', text.lower()))
    if words.intersection(tr_words):
        return "tr"
    return "tr"

def check_greeting(text: str):
    cleaned = re.sub(r'[^\w\s]', '', text.lower()).strip()
    tr_greetings = {"merhaba", "selam", "selamlar", "nasılsın", "nasıl gidiyor", "günaydın", "iyi günler", "iyi akşamlar", "naber", "merhabalarr", "nasilsin"}
    en_greetings = {"hello", "hi", "hey", "how are you", "good morning", "good afternoon", "good evening"}
    
    words = cleaned.split()
    if cleaned in tr_greetings or cleaned in ("bugun nasilsin", "nasilsin iyiyim", "merhaba nasilsin"):
        return "tr", "Merhaba, iyiyim! Bugün sana nasıl yardımcı olabilirim?, Genel Karşılama"
    if cleaned in en_greetings or cleaned in ("how are you today", "hello how are you"):
        return "en", "Hello, I am doing well! How can I help you today?, General Greeting"
    
    if len(words) <= 3:
        if any(w in tr_greetings for w in words):
            return "tr", "Merhaba, iyiyim! Bugün sana nasıl yardımcı olabilirim?, Genel Karşılama"
        if any(w in en_greetings for w in words):
            return "en", "Hello, I am doing well! How can I help you today?, General Greeting"
            
    return None, None

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
    raw_lang = (request.lang or "").strip().lower()
    user_lang = detect_language(user_query, raw_lang)
    
    if not user_query:
        raise HTTPException(status_code=400, detail="Question cannot be empty." if user_lang == "en" else "Soru boş olamaz.")

    # 1. Special greeting handler
    is_greeting_lang, greeting_response = check_greeting(user_query)
    if greeting_response:
        return {
            "answer": greeting_response,
            "source_found": True,
            "section": "Genel Karşılama" if is_greeting_lang == "tr" else "General Greeting"
        }

    # 2. Vector search with Nomic search_query: prefix and expanded query (k=4 for optimal speed & memory balance)
    search_query = f"search_query: {expand_search_query(user_query, user_lang)}"
    results = get_db().similarity_search_with_score(search_query, k=4)

    COSINE_THRESHOLD = 0.72 if user_lang == "tr" else 0.65
    filtered_results = [doc for doc, score in results if score <= COSINE_THRESHOLD]

    if not filtered_results:
        no_info_ans = "Bu bilgi personel el kitabında bulunmamaktadır." if user_lang == "tr" else "This information is not available in the staff handbook."
        source_str = "Northwestern Personel El Kitabı" if user_lang == "tr" else "Northwestern Staff Handbook"
        return {
            "answer": f"{no_info_ans}, {source_str}",
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
4. IF AND ONLY IF the context text contains no relevant information regarding the user's question topic, respond ONLY with: "Bu bilgi personel el kitabında bulunmamaktadır."
5. ABSOLUTELY NEVER generate Chinese, Japanese, or any language other than Turkish.
6. Do NOT wrap your output in ```markdown code blocks or include the word 'markdown' at the top of your response.
7. Keep answers concise and direct. Avoid long or complex unnecessary explanations.

CONTEXT TEXT:
{context_text}

USER QUESTION (IN TURKISH):
{user_query}
"""
    else:
        system_prompt = f"""You are the official Northwestern University Staff Handbook AI Assistant.
Below is the 'CONTEXT TEXT' extracted from the official Northwestern Staff Handbook.

STRICT RULES TO FOLLOW:
1. Answer the user's question accurately, concisely, and clearly based ONLY on the provided 'CONTEXT TEXT'.
2. Do NOT use outside knowledge, assumptions, or unverified claims not stated in the context text.
3. If the answer is NOT present in the provided context text, respond strictly with: "This information is not available in the staff handbook."
4. ABSOLUTELY NEVER generate Chinese, Japanese, or any language other than English.
5. Do NOT wrap your output in ```markdown code blocks or include the word 'markdown' at the top of your response.
6. Keep answers concise and direct. Avoid long or complex unnecessary explanations.

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
        raw_bot_response = response.json().get("response", "No response received.")
        
        bot_response = clean_markdown_wrappers(raw_bot_response)

        # Fallback if Chinese characters are detected
        if contains_chinese(bot_response):
            bot_response = (
                "Üzgünüm, sorunuz işlenirken bir dil hatası oluştu." if user_lang == "tr"
                else "Sorry, a language processing error occurred."
            )

        source_label = (
            f"Northwestern Personel El Kitabı (Bölüm: {matched_section})" if user_lang == "tr"
            else f"Northwestern Staff Handbook (Section: {matched_section})"
        )

        formatted_answer = f"{bot_response}, {source_label}"

        return {
            "answer": formatted_answer,
            "source_found": True,
            "section": matched_section
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM connection error ({OLLAMA_BASE_URL}): {str(e)}")


