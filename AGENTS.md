# 🤖 Project Agent Guidelines (`AGENTS.md`)

This repository is developed by a team using AI Coding Assistants (Antigravity IDE, Cursor, Windsurf, Claude Code, GitHub Copilot).
To prevent code degradation, architectural divergence, breaking changes, and merge conflicts, **all AI Agents and developers MUST strictly follow the rules defined in this file.**

---

## 📐 1. Architecture & Tech Stack Rules

- **Backend Framework:** FastAPI (`app/main.py`), served via Uvicorn in Docker. Do NOT add alternative web frameworks (e.g., Flask, Django).
- **Database & Vector Store:** Self-hosted **Supabase PostgreSQL 15+ with `pgvector`** extension (`db/schema.sql`). All document search MUST use `match_documents` RPC with `SECURITY INVOKER` and Row-Level Security (RLS). Do NOT add alternative vector databases (e.g., ChromaDB, Pinecone, Qdrant).
- **Authentication & Authorization:** Supabase GoTrue Auth with JWT tokens containing `app_metadata` (`role`, `department`, `clearance_level`). Database operations MUST enforce RLS via `SET LOCAL request.jwt.claims`.
- **LLM & Embeddings:** Host-level Ollama API bridge (`host.docker.internal:11434` / `nomic-embed-text` for embeddings, `qwen2.5:7b` for generation).
- **PDF & Document Ingestion:** PyMuPDF (`fitz`) and `pdfplumber` (`app/services/ingestion.py`) with SHA-256 hash deduplication, Markdown table extraction, and versioning.
- **Audit & Curation:** Asynchronous `audit_logs` tracking and `knowledge_staging` Flywheel curation (`app/services/metrics.py`).
- **Frontend:** Vanilla HTML/CSS/JavaScript in `static/index.html`. Do NOT introduce heavy frontend frameworks (React, Vue, Angular) unless explicitly requested.

---

## 🚫 2. Code Modification & Scope Rules (Refactoring Boundaries)

- **Modular Architecture:** Keep code cleanly separated in `app/schemas/`, `app/middleware/`, `app/services/`, and `db/`.
- **Minimal & Target-Specific Edits:** Only modify the lines/functions directly relevant to the task. Do NOT execute unauthorized refactoring.
- **Preserve Documentation:** Retain existing Python docstrings, type annotations, and code comments.
- **Environment Variables:** ALWAYS fetch configurations (URLs, keys, paths) using Pydantic Settings (`app/config.py`). NEVER hardcode passwords, API keys, or JWT secret keys.
- **No Swallowing Exceptions:** Catch specific exceptions and log full tracebacks. Do NOT silently ignore errors (`except: pass`).

---

## 🔐 3. Security & Data Protection

- Never commit secrets, passwords, `.env` files, or private keys to the Git repository.
- Rely on PostgreSQL Row Level Security (RLS) for multi-tenant data isolation; never bypass RLS in application logic.

---

## 🔄 4. Rule Changes & Architectural Modifications Procedure

`AGENTS.md` acts as the **Constitution of this repository**. Neither AI Agents nor individual developers may unilaterally modify project architecture or `AGENTS.md` rules.

If a major architectural change or rule update is needed:
1. Discuss the proposed change with the team.
2. Create a dedicated feature branch (e.g., `feature/update-agent-rules` or `feature/arch-upgrade`).
3. Open a Pull Request (PR) detailing the rationale.
4. Obtain review and approval from at least one team collaborator before merging to `main`.

