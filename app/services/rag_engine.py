import json
import time
from typing import List, Tuple, AsyncGenerator, Optional
from app.config import settings
from app.schemas.models import UserContext, ChunkCitation, QueryResponse
from app.services.ollama_client import ollama_service

async def search_relevant_chunks(
    user: UserContext,
    query: str,
    db_pool,
    match_count: int = settings.MATCH_COUNT,
    similarity_threshold: float = settings.SIMILARITY_THRESHOLD
) -> List[ChunkCitation]:
    """Kullanıcının JWT rol ve departman bağlamında RLS uyumlu vektör benzerlik araması yapar."""
    # 1. Sorunun embedding'ini al
    query_emb = await ollama_service.get_embedding(query)
    emb_str = "[" + ",".join(map(str, query_emb)) + "]"
    
    # 2. RLS bağlamını hazırla
    jwt_claims = json.dumps({
        "app_metadata": {
            "role": user.role,
            "department": user.department,
            "clearance_level": user.clearance_level
        }
    })

    claims_escaped = jwt_claims.replace("'", "''")
    citations = []
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Session seviyesinde JWT claims enjeksiyonu (Sıfır Sızıntı İlkesi)
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(f"SET LOCAL request.jwt.claims = '{claims_escaped}';")
            
            # match_documents RPC çağrısı
            rows = await conn.fetch(
                """
                SELECT id, document_id, content, department, min_clearance_level, similarity
                FROM match_documents($1::vector, $2, $3)
                """,
                emb_str, match_count, similarity_threshold
            )
            
            for r in rows:
                citations.append(
                    ChunkCitation(
                        id=str(r["id"]),
                        document_id=str(r["document_id"]),
                        content=r["content"],
                        department=r["department"],
                        min_clearance_level=r["min_clearance_level"],
                        similarity=float(r["similarity"])
                    )
                )
    return citations

def build_system_prompt(citations: List[ChunkCitation], lang: str) -> str:
    """RAG bağlamı ile zenginleştirilmiş sistem istemi oluşturur."""
    context_text = "\n\n---\n\n".join([c.content for c in citations])
    
    if lang == "tr":
        return f"""Sen Nirene AI Workspace & Enterprise RAG Yetkili Kurumsal Asistanısın.
Aşağıda veritabanından kullanıcının güvenlik yetkisine göre filtrelenmiş 'BAĞLAM METNİ' (CONTEXT TEXT) yer almaktadır.

KESİN VE TAVİZSİZ KURALLAR:
1. Yalnızca ve sadece aşağıda verilen BAĞLAM METNİ içindeki bilgilere dayanarak TÜRKÇE yanıt ver.
2. Bağlamda yer almayan veya doğrulanmamış şirket politikalarını asla uydurma.
3. Eğer sorunun cevabı bağlam metninde kesin olarak bulunmuyorsa, sadece: "Bu bilgi şirket politikalarında veya yetkiniz dahilindeki belgelerde bulunmamaktadır." yanıtını ver.
4. Yanıtı net, profesyonel, maddeli ve anlaşılır tut.

BAĞLAM METNİ:
{context_text}
"""
    else:
        return f"""You are the official Nirene AI Workspace & Enterprise RAG Assistant.
Below is the securely filtered 'CONTEXT TEXT' extracted from the official policy database.

STRICT RULES TO FOLLOW:
1. Answer the user's question accurately, concisely, and clearly based ONLY on the provided 'CONTEXT TEXT'.
2. Do NOT use outside knowledge, assumptions, or unverified claims.
3. If the answer is NOT present in the provided context text, respond strictly with: "This information is not available in the company policies or your authorized documents."
4. Keep answers concise, structured, and direct.

CONTEXT TEXT:
{context_text}
"""

async def execute_rag_query(
    user: UserContext,
    query: str,
    lang: str,
    session_id: Optional[str],
    db_pool
) -> QueryResponse:
    """Senkron / Non-streaming RAG sorgu yürütücüsü."""
    start_time = time.time()
    citations = await search_relevant_chunks(user, query, db_pool)
    
    if not citations:
        no_info = (
            "Bu bilgi şirket politikalarında veya yetkiniz dahilindeki belgelerde bulunmamaktadır." 
            if lang == "tr" else 
            "This information is not available in company policies or your authorized documents."
        )
        return QueryResponse(
            answer=no_info,
            source_found=False,
            citations=[],
            session_id=session_id,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

    system_prompt = build_system_prompt(citations, lang)
    bot_answer = await ollama_service.generate_response(system_prompt, query)
    exec_time = int((time.time() - start_time) * 1000)

    return QueryResponse(
        answer=bot_answer,
        source_found=True,
        citations=citations,
        session_id=session_id,
        execution_time_ms=exec_time
    )

async def stream_rag_query(
    user: UserContext,
    query: str,
    lang: str,
    session_id: Optional[str],
    db_pool
) -> AsyncGenerator[str, None]:
    """Server-Sent Events (SSE) streaming formatında RAG yanıtı üretir."""
    citations = await search_relevant_chunks(user, query, db_pool)
    
    if not citations:
        no_info = (
            "Bu bilgi personel el kitabında veya yetkiniz dahilindeki belgelerde bulunmamaktadır." 
            if lang == "tr" else 
            "This information is not available in the staff handbook or your authorized documents."
        )
        payload = json.dumps({"type": "meta", "source_found": False, "citations": []})
        yield f"data: {payload}\n\n"
        yield f"data: {json.dumps({'type': 'token', 'token': no_info})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Metadata olayını gönder
    meta_payload = json.dumps({
        "type": "meta",
        "source_found": True,
        "citations": [c.dict() for c in citations]
    })
    yield f"data: {meta_payload}\n\n"

    # LLM token akışını gönder
    system_prompt = build_system_prompt(citations, lang)
    async for token in ollama_service.stream_response(system_prompt, query):
        yield f"data: {json.dumps({'type': 'token', 'token': token})}\n\n"

    yield "data: [DONE]\n\n"
