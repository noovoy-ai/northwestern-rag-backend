import uuid
from typing import List, Optional, Dict, Any
from app.schemas.models import UserContext
from app.services.ollama_client import ollama_service

async def record_audit_and_metrics(
    user: UserContext,
    session_id: Optional[str],
    query_text: str,
    retrieved_chunk_ids: List[str],
    response_text: str,
    execution_time_ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    db_pool
) -> Optional[str]:
    """Sorgu denetim kaydını ve kullanıcı aktivite profilini asenkron olarak günceller."""
    try:
        clean_user_id = uuid.UUID(user.user_id) if isinstance(user.user_id, str) and len(user.user_id) == 36 else uuid.uuid5(uuid.NAMESPACE_DNS, user.user_id)
        clean_session_id = uuid.UUID(session_id) if session_id and len(session_id) == 36 else None
        clean_chunk_ids = [uuid.UUID(cid) for cid in retrieved_chunk_ids if len(cid) == 36]

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # 1. audit_logs Kaydı
                audit_id = await conn.fetchval(
                    """
                    INSERT INTO audit_logs (
                        user_id, session_id, query_text, retrieved_chunk_ids,
                        response_text, execution_time_ms, prompt_tokens, completion_tokens
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    clean_user_id, clean_session_id, query_text, clean_chunk_ids,
                    response_text, execution_time_ms, prompt_tokens, completion_tokens
                )

                # 2. user_profiles Güncellemesi (Upsert)
                await conn.execute(
                    """
                    INSERT INTO user_profiles (
                        user_id, email, role_name, department, clearance_level,
                        total_queries, total_prompt_tokens, total_completion_tokens,
                        activity_score, trust_score, risk_score, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, 1, $6, $7, 1.0, 100.0, 0.0, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        total_queries = user_profiles.total_queries + 1,
                        total_prompt_tokens = user_profiles.total_prompt_tokens + $6,
                        total_completion_tokens = user_profiles.total_completion_tokens + $7,
                        activity_score = user_profiles.activity_score + 1.0,
                        updated_at = NOW()
                    """,
                    clean_user_id, user.email, user.role, user.department, user.clearance_level,
                    prompt_tokens, completion_tokens
                )
                return str(audit_id)
    except Exception as e:
        print(f"[ERROR] Audit log kaydı sırasında hata: {e}")
        return None

async def save_feedback_and_curation(
    user: UserContext,
    audit_log_id: Optional[str],
    query_text: str,
    response_text: str,
    feedback: int,
    feedback_notes: Optional[str],
    corrected_answer: Optional[str],
    department: str,
    min_clearance_level: int,
    db_pool
) -> Optional[str]:
    """Kullanıcı geri bildirimini audit_logs'a işler ve knowledge_staging havuzuna aktarır."""
    staging_id = None
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # 1. audit_logs güncelle
                if audit_log_id and len(audit_log_id) == 36:
                    await conn.execute(
                        """
                        UPDATE audit_logs 
                        SET user_feedback = $1, feedback_notes = $2
                        WHERE id = $3
                        """,
                        feedback, feedback_notes or corrected_answer, uuid.UUID(audit_log_id)
                    )

                # 2. Olumlu (+1) veya düzeltilmiş yanıtları Kürasyon havuzuna ekle
                target_answer = corrected_answer.strip() if corrected_answer else response_text.strip()
                if feedback == 1 or corrected_answer:
                    clean_audit_id = uuid.UUID(audit_log_id) if audit_log_id and len(audit_log_id) == 36 else None
                    staging_id = await conn.fetchval(
                        """
                        INSERT INTO knowledge_staging (
                            audit_log_id, original_query, verified_answer,
                            department, min_clearance_level, status
                        ) VALUES ($1, $2, $3, $4, $5, 'pending')
                        RETURNING id
                        """,
                        clean_audit_id, query_text, target_answer, department, min_clearance_level
                    )
        return str(staging_id) if staging_id else None
    except Exception as e:
        print(f"[ERROR] Feedback/Curation kayıt hatası: {e}")
        return None

async def approve_curation_item(
    staging_id: str,
    verified_answer: Optional[str],
    approved_by: str,
    db_pool
) -> Dict[str, Any]:
    """Kürasyon havuzundaki öğeyi onaylar ve document_chunks tablosuna kalıcı vektör olarak ekler."""
    clean_staging_id = uuid.UUID(staging_id)
    clean_approved_by = uuid.UUID(approved_by) if len(approved_by) == 36 else uuid.uuid5(uuid.NAMESPACE_DNS, approved_by)

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # 1. Bilgileri getir
            item = await conn.fetchrow(
                "SELECT * FROM knowledge_staging WHERE id = $1 AND status = 'pending'",
                clean_staging_id
            )
            if not item:
                raise Exception("Onaylanacak bekleyen kürasyon kaydı bulunamadı.")

            final_answer = verified_answer.strip() if verified_answer else item["verified_answer"]
            curated_content = f"Soru: {item['original_query']}\nDoğrulanmış Cevap: {final_answer}"

            # 2. knowledge_staging durumunu güncelle
            await conn.execute(
                """
                UPDATE knowledge_staging 
                SET status = 'approved', verified_answer = $1, approved_by = $2, approved_at = NOW()
                WHERE id = $3
                """,
                final_answer, clean_approved_by, clean_staging_id
            )

            # 3. Genel kürasyon doküman kaydı oluştur/bul
            doc_id = await conn.fetchval(
                """
                INSERT INTO documents (title, file_hash, department, min_clearance_level, version, is_active, uploaded_by)
                VALUES ('Curated Q&A Knowledge Base', 'curated_hash_master', $1, $2, 1, TRUE, $3)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                item["department"], item["min_clearance_level"], clean_approved_by
            )
            if not doc_id:
                doc_id = await conn.fetchval(
                    "SELECT id FROM documents WHERE title = 'Curated Q&A Knowledge Base' LIMIT 1"
                )

            # 4. Embedding üret ve document_chunks tablosuna ekle
            embedding = await ollama_service.get_embedding(curated_content)
            emb_str = "[" + ",".join(map(str, embedding)) + "]"

            chunk_id = await conn.fetchval(
                """
                INSERT INTO document_chunks (
                    document_id, content, chunk_index, department,
                    min_clearance_level, is_active, source_type, metadata, embedding
                ) VALUES ($1, $2, 0, $3, $4, TRUE, 'curated_qa', $5::jsonb, $6::vector)
                RETURNING id
                """,
                doc_id or clean_staging_id,
                curated_content,
                item["department"],
                item["min_clearance_level"],
                f'{{"staging_id": "{staging_id}", "source": "knowledge_flywheel"}}',
                emb_str
            )

            return {
                "staging_id": staging_id,
                "chunk_id": str(chunk_id),
                "status": "approved",
                "department": item["department"]
            }
