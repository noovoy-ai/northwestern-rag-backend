import os
import re
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

MD_FILE_PATH = os.getenv("MD_FILE_PATH", "handbook_vectordb_ready.md")
DB_DIR = os.getenv("DB_DIR", "./chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "staff_handbook")
OLLAMA_MODEL = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

def parse_metadata_from_text(text_content: str) -> dict:
    """Markdown bloklarındaki metadata satırlarını ayıklar."""
    meta = {}
    pattern = r">\s*`?([a-zA-Z0-9_]+)`?\s*:\s*(.+)"
    matches = re.findall(pattern, text_content)
    for key, value in matches:
        meta[key.strip()] = value.strip()
    return meta

def load_and_split_enriched_markdown(file_path: str):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"HATA: '{file_path}' dosyası bulunamadı. Lütfen dosya adını kontrol edin.")

    with open(file_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # 1. Başlık Seviyelerine Göre Bölme
    headers_to_split_on = [
        ("#", "section_title"),
        ("##", "subsection_title"),
        ("###", "topic_title"),
    ]
    
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on, 
        strip_headers=False
    )
    header_splits = markdown_splitter.split_text(raw_text)

    # 2. Metadata Ayıklama ve Doküman Zenginleştirme
    enriched_docs = []
    for doc in header_splits:
        extracted_meta = parse_metadata_from_text(doc.page_content)
        combined_meta = {**doc.metadata, **extracted_meta}
        
        headers = [combined_meta.get(k) for k in ["section_title", "subsection_title", "topic_title"] if combined_meta.get(k)]
        header_prefix = " > ".join(headers) if headers else ""
        keywords = combined_meta.get("keywords_en", "")
        prefix_text = f"Context: [{header_prefix}] ({keywords})\n" if header_prefix else ""
        
        enriched_content = f"{prefix_text}{doc.page_content}"
        
        enriched_docs.append(
            Document(
                page_content=enriched_content,
                metadata=combined_meta
            )
        )

    # 3. İkinci Aşama Parçalama (Boyut Sınırlaması)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150
    )
    final_chunks = text_splitter.split_documents(enriched_docs)
    print(f"[INFO] Toplam {len(final_chunks)} adet zenginleştirilmiş vektör parçası oluşturuldu.")
    return final_chunks

def rebuild_vector_db():
    print("[INFO] Zenginleştirilmiş Markdown dosyası işleniyor...")
    chunks = load_and_split_enriched_markdown(MD_FILE_PATH)
    
    base_url = os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
    embedding_model = os.getenv("EMBEDDING_MODEL_NAME", OLLAMA_MODEL)
    db_dir = os.getenv("DB_DIR", DB_DIR)
    collection_name = os.getenv("COLLECTION_NAME", COLLECTION_NAME)

    print(f"[INFO] Embedding modeli hazırlanıyor ('{embedding_model}') - URL: {base_url}...")
    embeddings = OllamaEmbeddings(
        model=embedding_model,
        base_url=base_url
    )

    vector_store = Chroma(
        persist_directory=db_dir,
        embedding_function=embeddings,
        collection_name=collection_name,
        collection_metadata={"hnsw:space": "cosine"}
    )
    
    try:
        vector_store.delete_collection()
        print("[INFO] Eski koleksiyon temizlendi.")
    except Exception as e:
        print(f"[INFO] Koleksiyon temizleme uyarısı: {e}")

    vector_store = Chroma(
        persist_directory=db_dir,
        embedding_function=embeddings,
        collection_name=collection_name,
        collection_metadata={"hnsw:space": "cosine"}
    )
    vector_store.add_documents(chunks)
    
    print("\n[BAŞARILI] Vektör veritabanı yeni Markdown verisetiyle başarıyla güncellendi!")
    return len(chunks)

if __name__ == "__main__":
    rebuild_vector_db()
