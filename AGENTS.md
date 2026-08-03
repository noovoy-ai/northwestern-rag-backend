# 🤖 Project Agent Guidelines (`AGENTS.md`)

This repository is developed by a team using AI Coding Assistants (Antigravity IDE, Cursor, Windsurf, Claude Code, GitHub Copilot).
To prevent code degradation, architectural divergence, breaking changes, and merge conflicts, **all AI Agents and developers MUST strictly follow the rules defined in this file.**

---

## 📐 1. Architecture & Tech Stack Rules

- **Backend Framework:** FastAPI (`main.py`), served via Uvicorn. Do NOT add alternative web frameworks (e.g., Flask, Django).
- **Vector Database:** ChromaDB (`chromadb`), persisted in `./chroma_db` backed by SQLite3. Do NOT add alternative vector databases (e.g., Pinecone, Qdrant, Weaviate).
- **LLM & Embeddings:** Ollama API (`nomic-embed-text` for embeddings, `qwen2.5:7b` for generation).
- **Text Processing & Chunking:** LangChain (`langchain-text-splitters`, `MarkdownHeaderTextSplitter`, `RecursiveCharacterTextSplitter`).
- **Authentication:** JWT with `python-jose` using `HS256`.
- **Frontend:** Vanilla HTML/CSS/JavaScript in `static/index.html`. Do NOT introduce heavy frontend frameworks (React, Vue, Angular) unless explicitly requested.

---

## 🚫 2. Code Modification & Scope Rules (Refactoring Boundaries)

- **Minimal & Target-Specific Edits:** Only modify the lines/functions directly relevant to the task. Do NOT rewrite entire files, reformat unchanged functions, or execute unauthorized refactoring.
- **Preserve Documentation:** Retain existing Python docstrings, type annotations, and Turkish code comments.
- **Environment Variables:** ALWAYS fetch configurations (URLs, keys, paths) using `os.getenv()`. NEVER hardcode passwords, API keys, or JWT secret keys.
- **No Swallowing Exceptions:** Catch specific exceptions and log full tracebacks. Do NOT silently ignore errors (`except: pass`).

---

## 🔐 3. Security & Data Protection

- Never commit secrets, passwords, `.env` files, or private keys to Git repository.
- Use default fallback environment values only for local development (`os.getenv("SECRET_KEY", "...")`).

---

## 🔄 4. Rule Changes & Architectural Modifications Procedure

`AGENTS.md` acts as the **Constitution of this repository**. Neither AI Agents nor individual developers may unilaterally modify project architecture or `AGENTS.md` rules.

If a major architectural change or rule update is needed:
1. Discuss the proposed change with the team.
2. Create a dedicated feature branch (e.g., `feature/update-agent-rules` or `feature/arch-upgrade`).
3. Open a Pull Request (PR) detailing the rationale.
4. Obtain review and approval from at least one team collaborator before merging to `main`.
