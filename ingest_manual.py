import os
import json
import shutil
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_core.documents import Document

DB_DIR = "./chroma_db"
COLLECTION_NAME = "staff_handbook"
JSON_FILE = "manual_chunks.json"

if os.path.exists(DB_DIR):
    shutil.rmtree(DB_DIR)

with open(JSON_FILE, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

documents = []
for item in raw_data:
    metadata = item.get("metadata", {})
    clean_metadata = {}
    for key, value in metadata.items():
        if isinstance(value, list):
            clean_metadata[key] = ", ".join(value)
        else:
            clean_metadata[key] = value

    # Nomic embedding modeli için ön ek ekleme
    prefixed_content = f"search_document: {item['document']}"

    doc = Document(
        page_content=prefixed_content,
        metadata=clean_metadata
    )
    documents.append(doc)

embeddings = OllamaEmbeddings(
    model="nomic-embed-text",
    base_url="http://localhost:11434"
)

# Collection uzayını COSINE olarak belirleme
vector_store = Chroma.from_documents(
    documents=documents,
    embedding=embeddings,
    persist_directory=DB_DIR,
    collection_name=COLLECTION_NAME,
    collection_metadata={"hnsw:space": "cosine"}
)

print(f"Toplam {len(documents)} chunk Cosine metriği ile yüklendi.")
