import io
import hashlib
from typing import List, Dict, Any
import fitz  # PyMuPDF
import pdfplumber
from fastapi import HTTPException
from app.services.ollama_client import ollama_service

def compute_sha256(file_bytes: bytes) -> str:
    """PDF dosyasının SHA-256 hash özetini çıkarır."""
    return hashlib.sha256(file_bytes).hexdigest()

def extract_text_and_tables_from_pdf(file_bytes: bytes) -> List[Dict[str, Any]]:
    """PyMuPDF ve pdfplumber ile PDF'ten metin ve Markdown tablolarını çıkarır."""
    pages_data = []
    
    # 1. pdfplumber ile tabloları yakala
    tables_by_page = {}
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                md_tables = []
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    # Tabloyu Markdown formatına dönüştür
                    headers = [str(c or '').strip().replace('\n', ' ') for c in table[0]]
                    header_line = "| " + " | ".join(headers) + " |"
                    separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
                    row_lines = []
                    for row in table[1:]:
                        clean_row = [str(c or '').strip().replace('\n', ' ') for c in row]
                        row_lines.append("| " + " | ".join(clean_row) + " |")
                    
                    md_table = "\n".join([header_line, separator_line] + row_lines)
                    md_tables.append(md_table)
                if md_tables:
                    tables_by_page[page_num] = "\n\n".join(md_tables)
    except Exception as e:
        print(f"[WARN] pdfplumber tablo ayrıştırma uyarısı: {e}")

    # 2. PyMuPDF (fitz) ile ana metinleri çıkar
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        table_text = tables_by_page.get(page_num, "")
        
        combined_page_text = text
        if table_text:
            combined_page_text = f"{text}\n\n[TABLOLAR]:\n{table_text}"

        pages_data.append({
            "page_number": page_num + 1,
            "text": combined_page_text,
            "is_scanned": len(text) < 50
        })

    return pages_data

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 100) -> List[str]:
    """Basit ve güvenli metin parçalama (chunking)."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += (chunk_size - overlap)
        if start >= len(text):
            break
    return chunks

async def process_and_ingest_pdf(
    file_bytes: bytes,
    title: str,
    department: str,
    min_clearance_level: int,
    uploaded_by: str,
    db_pool
) -> Dict[str, Any]:
    """PDF dosyasını ayrıştırır, hash kontrolü yapar, embedding üretir ve veritabanına kaydeder."""
    file_hash = compute_sha256(file_bytes)
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # 1. SHA-256 Çakışma Kontrolü
            existing_doc = await conn.fetchrow(
                "SELECT id, title FROM documents WHERE file_hash = $1 AND is_active = TRUE",
                file_hash
            )
            if existing_doc:
                raise HTTPException(
                    status_code=409,
                    detail=f"Bu dosya içeriği zaten '{existing_doc['title']}' adıyla kayıtlıdır."
                )

            # 2. Eski aynı başlıklı dokümanları arşivle (Soft-delete & Versioning)
            prev_doc = await conn.fetchrow(
                "SELECT id, version FROM documents WHERE title = $1 AND is_active = TRUE ORDER BY version DESC LIMIT 1",
                title
            )
            new_version = (prev_doc["version"] + 1) if prev_doc else 1
            
            if prev_doc:
                await conn.execute("UPDATE documents SET is_active = FALSE WHERE title = $1", title)
                await conn.execute(
                    "UPDATE document_chunks SET is_active = FALSE WHERE document_id = $1",
                    prev_doc["id"]
                )

            # 3. Yeni Doküman Kaydını Oluştur
            doc_id = await conn.fetchval(
                """
                INSERT INTO documents (title, file_hash, department, min_clearance_level, version, is_active, uploaded_by)
                VALUES ($1, $2, $3, $4, $5, TRUE, $6)
                RETURNING id
                """,
                title, file_hash, department, min_clearance_level, new_version, uploaded_by
            )

            # 4. Metin Çıkarma ve Chunking
            pages_data = extract_text_and_tables_from_pdf(file_bytes)
            all_chunks = []
            chunk_index = 0
            
            for page in pages_data:
                p_text = page["text"]
                if not p_text.strip():
                    continue
                p_chunks = chunk_text(p_text, chunk_size=750, overlap=100)
                for chunk in p_chunks:
                    prefix = f"Context: [{title} | Bölüm: {department.upper()} | Yetki: {min_clearance_level} | Sayfa: {page['page_number']}]\n"
                    enriched_content = f"{prefix}{chunk}"
                    all_chunks.append({
                        "content": enriched_content,
                        "chunk_index": chunk_index,
                        "page_number": page["page_number"]
                    })
                    chunk_index += 1

            if not all_chunks:
                raise HTTPException(status_code=400, detail="PDF dosyasından okunabilir metin çıkarılamadı.")

            # 5. Toplu Vektörleştirme (Batch Embedding)
            raw_contents = [c["content"] for c in all_chunks]
            embeddings = await ollama_service.get_embeddings_batch(raw_contents)

            # 6. document_chunks Tablosuna Toplu Kayıt
            for chunk_data, emb in zip(all_chunks, embeddings):
                emb_str = "[" + ",".join(map(str, emb)) + "]"
                await conn.execute(
                    """
                    INSERT INTO document_chunks (
                        document_id, content, chunk_index, department, 
                        min_clearance_level, is_active, source_type, metadata, embedding
                    ) VALUES ($1, $2, $3, $4, $5, TRUE, 'pdf', $6::jsonb, $7::vector)
                    """,
                    doc_id,
                    chunk_data["content"],
                    chunk_data["chunk_index"],
                    department,
                    min_clearance_level,
                    f'{{"page": {chunk_data["page_number"]}, "title": "{title}"}}',
                    emb_str
                )

    return {
        "id": str(doc_id),
        "title": title,
        "file_hash": file_hash,
        "department": department,
        "min_clearance_level": min_clearance_level,
        "version": new_version,
        "is_active": True,
        "total_chunks": len(all_chunks)
    }
