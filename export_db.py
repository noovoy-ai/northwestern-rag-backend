import chromadb
import json

client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_collection(name="staff_handbook")

data = collection.get(include=["documents", "metadatas"])

export_list = []
for i in range(len(data["ids"])):
    export_list.append({
        "id": data["ids"][i],
        "metadata": data["metadatas"][i],
        "document": data["documents"][i]
    })

with open("vectordb_export.json", "w", encoding="utf-8") as f:
    json.dump(export_list, f, ensure_ascii=False, indent=2)

print(f"Toplam {len(export_list)} chunk 'vectordb_export.json' dosyasina aktarildi.")
