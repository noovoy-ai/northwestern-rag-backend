import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Nirene AI Workspace & Enterprise RAG API"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    HOST_PORT: int = 8005
    
    # Supabase PostgreSQL
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/postgres")
    
    # GoTrue & Güvenlik
    GOTRUE_URL: str = os.getenv("GOTRUE_URL", "http://auth:9999")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-jwt-token-key-change-me-32-chars")
    ALGORITHM: str = "HS256"
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin*123!")
    STAFF_PASSWORD: str = os.getenv("STAFF_PASSWORD", "nu2026pass")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")
    
    # Ollama Modelleri
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    
    # RAG Parametreleri
    SIMILARITY_THRESHOLD: float = 0.40
    MATCH_COUNT: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
