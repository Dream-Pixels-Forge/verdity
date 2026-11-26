# SG-23 — Real embeddings and a vector store for the shared semantic index

**Type:** Feature · **Size:** L · **Depends on:** SG-14 · **Branch:** `feat/sg-23-embeddings`

## Why
Constraint #4 ("one shared semantic index") is the basis for cross-file findings: "this new endpoint skips the auth decorator every other route uses", "this helper duplicates `utils/x.py`". Today:
- `DevEmbeddingGenerator` (`semantic_index.py:76`) is the only implementation. Its vectors are content hashes, so similarity search is effectively random.
- `semantic_index_postgres_url` is in `Settings`, but nothing reads it. Docstrings say "pgvector in production", which doesn't exist yet.
- Search is a brute-force cosine scan over every chunk stored as JSON text (`semantic_index.py:~265` notes this).

## Scope
1. **Embedding providers** behind the existing `EmbeddingGenerator` ABC:
   - `LocalEmbeddingGenerator`: `sentence-transformers` code model (optional extra `verdity[embeddings]`), CPU-friendly, no data leaves the box (the default for private code);
   - `GatewayEmbeddingGenerator`: through `llm_gateway.py` (Ollama `/api/embed`, OpenAI-compatible `/v1/embeddings`), metered via `TokenEconomicsService` (constraint #8).
2. **Vector stores** behind a `VectorStore` protocol:
   - `SqliteVecStore` using the `sqlite-vec` extension when available, otherwise the current brute-force fallback;
   - `PgVectorStore` driven by `semantic_index_postgres_url` (HNSW index, `repo_id` partition column).
3. **Chunking:** syntax-aware chunks (function/class) via `tree-sitter` where the grammar is installed, otherwise the current line windows. Store `symbol`, `kind` and `path` for the symbol graph.
4. **Incremental indexing:** reuse the existing file-hash metadata. Index on `push` to the default branch (a new reviewable trigger) so PR reviews query a warm index.
5. **Privacy:** never send code to a remote embedder unless `embeddings_allow_remote=true`. Run secret redaction (already in `llm_enricher`) before embedding.

## Plan (TDD)
- Red: a retrieval-quality test with a tiny fixture repo of 20 functions. The query "verify jwt token" returns `auth/jwt.py::verify` in the top 3 with the local model. Mark it `slow` and skip it when the extra isn't installed.
- Red: store contract tests run on both vector stores (pg via a CI service container).
- Red: remote embedder disabled by default → `ConfigError` if selected without the opt-in.

## Definition of done
- [ ] At least one specialist (security: "missing auth decorator vs. siblings") uses retrieval and has a test proving the cross-file finding.
- [ ] README documents the providers and the privacy defaults.

## Commit
`feat(index): pluggable embeddings (local/gateway) and vector stores (sqlite-vec/pgvector)`
