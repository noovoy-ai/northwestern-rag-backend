-- Gerekli Şemalar ve Roller
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS extensions;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'supabase_admin') THEN
    CREATE ROLE supabase_admin WITH SUPERUSER CREATEDB CREATEROLE LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN;
  END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";



-- 1. Ana Doküman Tablosu
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    department VARCHAR(50) NOT NULL,       -- 'hukuk', 'finans', 'ik', 'genel'
    min_clearance_level INT NOT NULL,       -- 10: User, 50: Admin, 100: Super Admin
    version INT DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    replaced_by_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    uploaded_by UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_docs_active ON documents(is_active);

-- 2. Doküman Vektör Parçaları (Chunks) Tablosu
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    chunk_index INT NOT NULL,
    department VARCHAR(50) NOT NULL,
    min_clearance_level INT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    source_type VARCHAR(30) DEFAULT 'pdf', -- 'pdf', 'curated_qa', 'api'
    metadata JSONB DEFAULT '{}'::jsonb,
    embedding vector(768)                 -- nomic-embed-text boyutu 768
);

CREATE INDEX IF NOT EXISTS idx_chunks_hnsw ON document_chunks 
USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_dept_level ON document_chunks(department, min_clearance_level);

-- 3. Kullanıcı Profil ve Skor Tablosu
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY,
    email TEXT NOT NULL,
    role_name VARCHAR(50) NOT NULL,        -- 'user-hukuk', 'admin-finans', 'super_admin'
    department VARCHAR(50) NOT NULL,
    clearance_level INT NOT NULL DEFAULT 10,
    total_queries INT DEFAULT 0,
    total_prompt_tokens BIGINT DEFAULT 0,
    total_completion_tokens BIGINT DEFAULT 0,
    activity_score FLOAT DEFAULT 0.0,
    trust_score FLOAT DEFAULT 100.0,
    risk_score FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Kapsamlı Denetim İzi (Audit Log) Tablosu
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID,
    query_text TEXT NOT NULL,
    retrieved_chunk_ids UUID[],
    response_text TEXT NOT NULL,
    execution_time_ms INT NOT NULL,
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    user_feedback SMALLINT DEFAULT 0,      -- 1: Faydalı, -1: Hatalı, 0: Nötr
    feedback_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Kurumsal Bilgi Havuzu ve Kürasyon Tablosu (Knowledge Flywheel)
CREATE TABLE IF NOT EXISTS knowledge_staging (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_log_id UUID REFERENCES audit_logs(id),
    original_query TEXT NOT NULL,
    verified_answer TEXT NOT NULL,
    department VARCHAR(50) NOT NULL,
    min_clearance_level INT NOT NULL DEFAULT 10,
    status VARCHAR(20) DEFAULT 'pending',  -- 'pending', 'approved', 'rejected'
    approved_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ
);

-- -------------------------------------------------------------
-- ROW LEVEL SECURITY (RLS) POLİTİKALARI
-- -------------------------------------------------------------
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_staging ENABLE ROW LEVEL SECURITY;

-- documents Tablosu RLS
DROP POLICY IF EXISTS "Documents_Select_Policy" ON documents;
CREATE POLICY "Documents_Select_Policy" ON documents
FOR SELECT USING (
    coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'role'), '') = 'super_admin'
    OR (
        is_active = TRUE 
        AND (department = coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'department'), '') OR department = 'genel')
        AND min_clearance_level <= coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'clearance_level')::int, 10)
    )
);

DROP POLICY IF EXISTS "Documents_SuperAdmin_All" ON documents;
CREATE POLICY "Documents_SuperAdmin_All" ON documents
FOR ALL USING (coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'role'), '') = 'super_admin');

-- document_chunks Tablosu RLS
DROP POLICY IF EXISTS "Chunks_Select_Policy" ON document_chunks;
CREATE POLICY "Chunks_Select_Policy" ON document_chunks
FOR SELECT USING (
    coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'role'), '') = 'super_admin'
    OR (
        is_active = TRUE 
        AND (department = coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'department'), '') OR department = 'genel')
        AND min_clearance_level <= coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'clearance_level')::int, 10)
    )
);

DROP POLICY IF EXISTS "Chunks_SuperAdmin_All" ON document_chunks;
CREATE POLICY "Chunks_SuperAdmin_All" ON document_chunks
FOR ALL USING (coalesce((current_setting('request.jwt.claims', true)::jsonb -> 'app_metadata' ->> 'role'), '') = 'super_admin');

-- -------------------------------------------------------------
-- GÜVENLİ VEKTÖR ARAMA RPC FONKSİYONU (SQLi & Context Leak Proof)
-- -------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector(768),
    match_count INT DEFAULT 5,
    similarity_threshold FLOAT DEFAULT 0.40
)
RETURNS TABLE (
    id UUID,
    document_id UUID,
    content TEXT,
    department VARCHAR,
    min_clearance_level INT,
    similarity FLOAT
)
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.document_id,
        dc.content,
        dc.department,
        dc.min_clearance_level,
        (1 - (dc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM document_chunks dc
    WHERE (1 - (dc.embedding <=> query_embedding)) >= similarity_threshold
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
