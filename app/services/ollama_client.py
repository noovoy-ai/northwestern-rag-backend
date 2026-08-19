import json
import httpx
from typing import List, AsyncGenerator
from app.config import settings

class OllamaClient:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip('/')
        self.embedding_model = settings.EMBEDDING_MODEL
        self.llm_model = settings.LLM_MODEL

    async def get_embedding(self, text: str) -> List[float]:
        """Tekil metin için Ollama üzerinden embedding vektörü üretir."""
        url = f"{self.base_url}/api/embeddings"
        payload = {
            "model": self.embedding_model,
            "prompt": text
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("embedding", [])

    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Toplu metin listesi için embedding vektörleri üretir."""
        # Ollama /api/embed batch endpoint'ini dener, desteklenmiyorsa tek tek üretir
        url = f"{self.base_url}/api/embed"
        payload = {
            "model": self.embedding_model,
            "input": texts
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("embeddings", [])
        except Exception:
            pass

        # Fallback: tek tek üret
        results = []
        for t in texts:
            emb = await self.get_embedding(t)
            results.append(emb)
        return results

    async def generate_response(self, system_prompt: str, user_query: str) -> str:
        """LLM üzerinden tam yanıt üretir (Non-streaming)."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.llm_model,
            "system": system_prompt,
            "prompt": user_query,
            "stream": False
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()

    async def stream_response(self, system_prompt: str, user_query: str) -> AsyncGenerator[str, None]:
        """LLM üzerinden Server-Sent Events (SSE) için streaming yanıt üretir."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.llm_model,
            "system": system_prompt,
            "prompt": user_query,
            "stream": True
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                yield token
                        except Exception:
                            continue

ollama_service = OllamaClient()
