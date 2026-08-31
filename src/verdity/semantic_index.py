"""
Semantic Index Service — the single shared data store for all specialists.

Architecture doc §2.4: one semantic index/data store serving all specialists.
No specialist may spin up its own vector store, cache, or metadata table.

Backed by Postgres + pgvector in production; SQLite + FTS5 in dev.
Provides:
  - Embeddings for semantic code search (chunk-level)
  - Symbol graph (call graph, import graph) for structural queries
  - Metadata cache (file hashes, last-indexed SHA) for incremental re-indexing
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# ── Data Classes ──────────────────────────────────────────────────────


@dataclass
class CodeChunk:
    """A chunk of source code indexed for semantic search."""

    chunk_id: str
    repo_id: str  # "owner/name"
    file_path: str
    start_line: int
    end_line: int
    content: str  # The actual source code text
    language: str  # e.g. "python", "typescript"
    symbols: list[str] = field(default_factory=list)  # functions/classes in this chunk
    embedding: list[float] | None = None  # populated after embedding


@dataclass
class SymbolEdge:
    """An edge in the symbol graph (call/import relationship)."""

    edge_id: str
    repo_id: str
    source_symbol: str  # e.g. "module.func" or "module.Class"
    target_symbol: str
    edge_type: str  # "calls" | "imports" | "implements" | "extends"


@dataclass
class FileMetadata:
    """Per-file metadata for incremental indexing."""

    repo_id: str
    file_path: str
    content_hash: str  # sha256 of file content
    last_indexed_sha: str | None = None  # git SHA of last successful index
    chunk_count: int = 0


# ── Embedding Generator (pluggable) ───────────────────────────────────


class EmbeddingGenerator:
    """
    Generates embeddings for code chunks.
    In prod: calls an embedding model API (OpenAI, SentenceTransformers, etc.)
    In dev: returns a deterministic placeholder vector for testing.
    """

    def embed_batch(self, chunks: list[CodeChunk]) -> list[list[float]]:
        """Return one embedding vector per chunk."""
        raise NotImplementedError


class DevEmbeddingGenerator(EmbeddingGenerator):
    """
    Deterministic placeholder embeddings for dev/testing.
    Uses chunk content hash to produce a reproducible 8-dim vector.
    """

    DIM = 8

    def embed_batch(self, chunks: list[CodeChunk]) -> list[list[float]]:
        vectors = []
        for chunk in chunks:
            h = hashlib.sha256(chunk.content.encode()).hexdigest()
            # Deterministic but content-dependent vector
            vec = [float(int(h[i * 2 : i * 2 + 2], 16)) / 255.0 for i in range(self.DIM)]
            # Normalize
            mag = (sum(x * x for x in vec) ** 0.5) or 1.0
            vectors.append([x / mag for x in vec])
        return vectors


# ── Semantic Index Service ────────────────────────────────────────────


class SemanticIndex:
    """
    Shared semantic index for all specialist agents.
    SQLite-backed for dev; replace with Postgres+pgvector for production.
    """

    SCHEMA_SQL = """
        -- Code chunks with embeddings
        CREATE TABLE IF NOT EXISTS code_chunks (
            chunk_id      TEXT PRIMARY KEY,
            repo_id       TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            start_line    INTEGER NOT NULL,
            end_line      INTEGER NOT NULL,
            content       TEXT NOT NULL,
            language      TEXT NOT NULL,
            symbols       TEXT DEFAULT '[]',
            embedding     TEXT,  -- JSON array of floats
            indexed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_repo    ON code_chunks(repo_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_file    ON code_chunks(repo_id, file_path);
        CREATE INDEX IF NOT EXISTS idx_chunks_lang    ON code_chunks(language);

        -- Symbol graph edges
        CREATE TABLE IF NOT EXISTS symbol_edges (
            edge_id       TEXT PRIMARY KEY,
            repo_id       TEXT NOT NULL,
            source_symbol TEXT NOT NULL,
            target_symbol TEXT NOT NULL,
            edge_type     TEXT NOT NULL,
            indexed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_edges_repo     ON symbol_edges(repo_id);
        CREATE INDEX IF NOT EXISTS idx_edges_source   ON symbol_edges(repo_id, source_symbol);
        CREATE INDEX IF NOT EXISTS idx_edges_target   ON symbol_edges(repo_id, target_symbol);

        -- File metadata for incremental indexing
        CREATE TABLE IF NOT EXISTS file_metadata (
            repo_id       TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            content_hash  TEXT NOT NULL,
            last_indexed_sha TEXT,
            chunk_count   INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (repo_id, file_path)
        );
    """

    def __init__(self, db_path: str = ":memory:", embedding_dim: int = 8) -> None:
        self._db_path = db_path
        self._embedding_dim = embedding_dim
        self._conn: Any = None
        self._embedder: EmbeddingGenerator = DevEmbeddingGenerator()

    async def connect(self) -> None:
        from verdity.async_sqlite import AsyncConnection

        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(self.SCHEMA_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Chunk Operations ──────────────────────────────────────────────

    async def upsert_chunks(self, chunks: list[CodeChunk]) -> int:
        """
        Index (or re-index) a batch of code chunks.
        Returns the number of chunks inserted/updated.
        Embeddings are generated automatically.
        """
        if not chunks:
            return 0

        # Generate embeddings
        embeddings = self._embedder.embed_batch(chunks)
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb

        inserted = 0
        for chunk in chunks:
            emb_json = json.dumps(chunk.embedding) if chunk.embedding else None
            symbols_json = json.dumps(chunk.symbols)

            await self._conn.execute(
                """
                INSERT INTO code_chunks
                    (chunk_id, repo_id, file_path, start_line, end_line,
                     content, language, symbols, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    content       = excluded.content,
                    embedding     = excluded.embedding,
                    symbols       = excluded.symbols,
                    indexed_at    = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                """,
                (
                    chunk.chunk_id,
                    chunk.repo_id,
                    chunk.file_path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.content,
                    chunk.language,
                    symbols_json,
                    emb_json,
                ),
            )
            inserted += 1

        await self._conn.commit()

        # Update file metadata
        file_stats: dict[tuple[str, str], int] = {}
        for chunk in chunks:
            key = (chunk.repo_id, chunk.file_path)
            file_stats[key] = file_stats.get(key, 0) + 1

        for (repo_id, file_path), count in file_stats.items():
            await self._conn.execute(
                """
                INSERT INTO file_metadata (repo_id, file_path, content_hash, chunk_count, updated_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                ON CONFLICT(repo_id, file_path) DO UPDATE SET
                    chunk_count = excluded.chunk_count,
                    updated_at  = excluded.updated_at
                """,
                (repo_id, file_path, "", count),
            )
        await self._conn.commit()
        return inserted

    async def delete_chunks_for_file(self, repo_id: str, file_path: str) -> int:
        """Remove all chunks for a file (used during incremental re-index)."""
        rows = await self._conn.execute(
            "SELECT chunk_id FROM code_chunks WHERE repo_id = ? AND file_path = ?",
            (repo_id, file_path),
        )
        count = len(rows)
        if count > 0:
            await self._conn.execute(
                "DELETE FROM code_chunks WHERE repo_id = ? AND file_path = ?",
                (repo_id, file_path),
            )
            await self._conn.commit()
        return count

    # ── Semantic Search ───────────────────────────────────────────────

    async def semantic_search(
        self,
        repo_id: str,
        query_vector: list[float],
        limit: int = 10,
        min_similarity: float = 0.3,
    ) -> list[dict[str, Any]]:
        """
        Find chunks most similar to the query vector using cosine similarity.

        SQLite lacks native vector operations, so we fetch candidate rows
        and compute cosine similarity in Python. For production workloads
        with large indexes, migrate to a vector-enabled store (pgvector,
        Qdrant, etc.).
        """
        dim = len(query_vector)
        rows = await self._conn.execute(
            """
            SELECT chunk_id, file_path, start_line, end_line, content,
                   language, symbols, embedding
            FROM code_chunks
            WHERE repo_id = ?
            ORDER BY indexed_at DESC
            LIMIT ?
            """,
            (repo_id, limit * 3),  # fetch more, filter by similarity in Python
        )

        import math

        query_mag = math.sqrt(sum(v * v for v in query_vector)) or 1.0
        results = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"]) if row["embedding"] else []
                if len(emb) != dim:
                    continue
                chunk_mag = math.sqrt(sum(v * v for v in emb)) or 1.0
                similarity = sum(q * e for q, e in zip(query_vector, emb)) / (query_mag * chunk_mag)
                if similarity >= min_similarity:
                    r = dict(row)
                    r["similarity"] = round(similarity, 4)
                    r["symbols"] = json.loads(r["symbols"]) if r["symbols"] else []
                    results.append(r)
                    if len(results) >= limit:
                        break
            except (json.JSONDecodeError, TypeError):
                continue

        return results

    async def search_by_text(
        self,
        repo_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Text-based search over chunk content (fallback when no embedding available).
        Uses LIKE for dev; in prod this would use the embedding search above.
        """
        rows = await self._conn.execute(
            """
            SELECT chunk_id, file_path, start_line, end_line, content,
                   language, symbols
            FROM code_chunks
            WHERE repo_id = ? AND content LIKE ?
            ORDER BY start_line ASC
            LIMIT ?
            """,
            (repo_id, f"%{query}%", limit),
        )
        results = []
        for row in rows:
            r = dict(row)
            r["symbols"] = json.loads(r["symbols"]) if r["symbols"] else []
            r["similarity"] = 1.0  # text match = high confidence
            results.append(r)
        return results

    # ── Incremental Re-indexing ───────────────────────────────────────

    async def get_files_needing_reindex(
        self,
        repo_id: str,
        file_hashes: dict[str, str],
    ) -> list[str]:
        """
        Compare incoming file hashes against stored metadata to find files
        that need re-indexing (new, changed, or deleted files).

        Args:
            repo_id: Repository identifier (owner/name)
            file_hashes: Dict of {file_path: sha256_hash} from the latest push

        Returns:
            List of file paths that need re-indexing
        """
        rows = await self._conn.execute(
            "SELECT file_path, content_hash FROM file_metadata WHERE repo_id = ?",
            (repo_id,),
        )
        stored = {r["file_path"]: r["content_hash"] for r in rows}

        needs_reindex: list[str] = []

        # New or changed files
        for file_path, content_hash in file_hashes.items():
            if file_path not in stored or stored[file_path] != content_hash:
                needs_reindex.append(file_path)

        # Deleted files (in stored but not in new hashes)
        for file_path in stored:
            if file_path not in file_hashes:
                needs_reindex.append(file_path)

        return needs_reindex

    async def mark_file_indexed(
        self,
        repo_id: str,
        file_path: str,
        content_hash: str,
        commit_sha: str | None = None,
    ) -> None:
        """Mark a file as successfully indexed with its content hash."""
        await self._conn.execute(
            """
            INSERT INTO file_metadata (repo_id, file_path, content_hash, last_indexed_sha, updated_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT(repo_id, file_path) DO UPDATE SET
                content_hash    = excluded.content_hash,
                last_indexed_sha = excluded.last_indexed_sha,
                updated_at      = excluded.updated_at
            """,
            (repo_id, file_path, content_hash, commit_sha),
        )
        await self._conn.commit()

    async def get_reindex_stats(self, repo_id: str) -> dict[str, Any]:
        """Get statistics about the index for a repository."""
        rows = await self._conn.execute(
            """
            SELECT
                COUNT(*) as total_files,
                SUM(chunk_count) as total_chunks,
                MIN(updated_at) as oldest_index,
                MAX(updated_at) as newest_index
            FROM file_metadata
            WHERE repo_id = ?
            """,
            (repo_id,),
        )
        row = rows[0] if rows else {}
        return {
            "repo_id": repo_id,
            "total_files": row.get("total_files", 0),
            "total_chunks": row.get("total_chunks", 0),
            "oldest_index": row.get("oldest_index"),
            "newest_index": row.get("newest_index"),
        }

    # ── Symbol Graph ──────────────────────────────────────────────────

    async def upsert_edges(self, edges: list[SymbolEdge]) -> int:
        """Insert or update symbol graph edges."""
        for edge in edges:
            await self._conn.execute(
                """
                INSERT INTO symbol_edges (edge_id, repo_id, source_symbol, target_symbol, edge_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(edge_id) DO NOTHING
                """,
                (
                    edge.edge_id,
                    edge.repo_id,
                    edge.source_symbol,
                    edge.target_symbol,
                    edge.edge_type,
                ),
            )
        await self._conn.commit()
        return len(edges)

    async def get_callers(self, repo_id: str, symbol: str, depth: int = 1) -> list[str]:
        """Find all symbols that call the given symbol (up to `depth` hops)."""
        results = {symbol}
        current_level = {symbol}
        for _ in range(depth):
            next_level: set[str] = set()
            for sym in current_level:
                rows = await self._conn.execute(
                    "SELECT source_symbol FROM symbol_edges WHERE repo_id = ? AND target_symbol = ? AND edge_type = 'calls'",
                    (repo_id, sym),
                )
                for r in rows:
                    if r["source_symbol"] not in results:
                        next_level.add(r["source_symbol"])
            current_level = next_level
            results.update(current_level)
        return sorted(results - {symbol})

    async def get_callees(self, repo_id: str, symbol: str, depth: int = 1) -> list[str]:
        """Find all symbols called by the given symbol (up to `depth` hops)."""
        results = {symbol}
        current_level = {symbol}
        for _ in range(depth):
            next_level: set[str] = set()
            for sym in current_level:
                rows = await self._conn.execute(
                    "SELECT target_symbol FROM symbol_edges WHERE repo_id = ? AND source_symbol = ? AND edge_type = 'calls'",
                    (repo_id, sym),
                )
                for r in rows:
                    if r["target_symbol"] not in results:
                        next_level.add(r["target_symbol"])
            current_level = next_level
            results.update(current_level)
        return sorted(results - {symbol})

    # ── Incremental Indexing Support ──────────────────────────────────

    async def get_modified_files(
        self,
        repo_id: str,
        new_head_sha: str,
    ) -> list[dict[str, Any]]:
        """
        Return files that have changed since last indexing (delta-aware).
        Used by the orchestrator to only re-embed changed files on pr.synchronize.
        """
        rows = await self._conn.execute(
            """
            SELECT file_path, content_hash, last_indexed_sha, chunk_count
            FROM file_metadata
            WHERE repo_id = ? AND (last_indexed_sha IS NULL OR last_indexed_sha != ?)
            """,
            (repo_id, new_head_sha),
        )
        return [dict(r) for r in rows]

    async def mark_indexed(
        self,
        repo_id: str,
        head_sha: str,
        file_changes: dict[str, str],  # file_path → new_content_hash
    ) -> None:
        """Update metadata after successful indexing of changed files."""
        for file_path, content_hash in file_changes.items():
            await self._conn.execute(
                """
                INSERT INTO file_metadata (repo_id, file_path, content_hash, last_indexed_sha, updated_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                ON CONFLICT(repo_id, file_path) DO UPDATE SET
                    content_hash     = excluded.content_hash,
                    last_indexed_sha = excluded.last_indexed_sha,
                    updated_at       = excluded.updated_at
                """,
                (repo_id, file_path, content_hash, head_sha),
            )
        await self._conn.commit()

    async def get_chunk_count(self, repo_id: str) -> int:
        """Return total number of chunks for a repo (monitoring metric)."""
        rows = await self._conn.execute(
            "SELECT COUNT(*) as cnt FROM code_chunks WHERE repo_id = ?",
            (repo_id,),
        )
        return rows[0]["cnt"] if rows else 0

    # ── Full-Codebase Context (v0.3.0) ────────────────────────────────

    async def get_full_context(
        self,
        repo_id: str,
        file_path: str,
        max_chunks: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get full context for a file including related chunks from other files.
        Uses symbol graph to find related code (callers, callees, imports).
        Returns chunks ordered by relevance for agent context windows.
        """
        # Get chunks from the target file
        file_chunks = await self._conn.execute(
            """
            SELECT chunk_id, file_path, start_line, end_line, content,
                   language, symbols
            FROM code_chunks
            WHERE repo_id = ? AND file_path = ?
            ORDER BY start_line ASC
            """,
            (repo_id, file_path),
        )

        # Collect symbols from this file
        file_symbols: set[str] = set()
        for chunk in file_chunks:
            symbols = json.loads(chunk["symbols"]) if chunk["symbols"] else []
            file_symbols.update(symbols)

        # Find related symbols via call graph
        related_symbols: set[str] = set()
        for symbol in file_symbols:
            # Find callers (who calls this symbol)
            callers = await self.get_callers(repo_id, symbol, depth=2)
            related_symbols.update(callers)

            # Find callees (what this symbol calls)
            callees = await self.get_callees(repo_id, symbol, depth=2)
            related_symbols.update(callees)

        # Get chunks containing related symbols
        related_chunks: list[dict[str, Any]] = []
        if related_symbols:
            symbol_list = list(related_symbols)[:50]  # limit to prevent explosion
            placeholders = ",".join("?" * len(symbol_list))
            rows = await self._conn.execute(
                f"""
                SELECT chunk_id, file_path, start_line, end_line, content,
                       language, symbols
                FROM code_chunks
                WHERE repo_id = ? AND file_path != ?
                ORDER BY indexed_at DESC
                LIMIT ?
                """,
                (repo_id, file_path, max_chunks),
            )
            for row in rows:
                chunk_symbols = json.loads(row["symbols"]) if row["symbols"] else []
                if any(s in symbol_list for s in chunk_symbols):
                    related_chunks.append(dict(row))

        # Combine and deduplicate
        all_chunks = [dict(c) for c in file_chunks]
        seen_ids = {c["chunk_id"] for c in all_chunks}
        for chunk in related_chunks:
            if chunk["chunk_id"] not in seen_ids:
                all_chunks.append(chunk)
                seen_ids.add(chunk["chunk_id"])

        return all_chunks[:max_chunks]

    async def get_file_dependencies(
        self,
        repo_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Get dependency information for a file."""
        # Get imports for this file
        rows = await self._conn.execute(
            """
            SELECT source_symbol, target_symbol
            FROM symbol_edges
            WHERE repo_id = ? AND source_symbol LIKE ? AND edge_type = 'imports'
            """,
            (repo_id, f"{file_path}%"),
        )

        imports = [dict(r) for r in rows]

        # Get reverse dependencies (who imports this file)
        reverse_rows = await self._conn.execute(
            """
            SELECT source_symbol, target_symbol
            FROM symbol_edges
            WHERE repo_id = ? AND target_symbol LIKE ? AND edge_type = 'imports'
            """,
            (repo_id, f"{file_path}%"),
        )

        reverse_deps = [dict(r) for r in reverse_rows]

        return {
            "file_path": file_path,
            "imports": imports,
            "imported_by": reverse_deps,
            "import_count": len(imports),
            "depended_on_by_count": len(reverse_deps),
        }

    async def index_full_repo(
        self,
        repo_id: str,
        files: list[dict[str, Any]],
        commit_sha: str | None = None,
    ) -> dict[str, Any]:
        """
        Index entire repository for full-codebase context.

        Args:
            repo_id: Repository identifier (owner/name)
            files: List of file dicts with keys: path, content, language
            commit_sha: Git commit SHA

        Returns:
            Indexing statistics
        """
        total_chunks = 0
        total_files = 0

        for file_info in files:
            file_path = file_info.get("path", "")
            content = file_info.get("content", "")
            language = file_info.get("language", "unknown")

            if not content or not file_path:
                continue

            # Chunk the file
            chunks = self._chunk_file(
                repo_id=repo_id,
                file_path=file_path,
                content=content,
                language=language,
            )

            if chunks:
                await self.upsert_chunks(chunks)
                total_chunks += len(chunks)
                total_files += 1

                # Mark as indexed
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                await self.mark_file_indexed(
                    repo_id, file_path, content_hash, commit_sha
                )

        return {
            "repo_id": repo_id,
            "files_indexed": total_files,
            "chunks_indexed": total_chunks,
            "commit_sha": commit_sha,
        }

    def _chunk_file(
        self,
        repo_id: str,
        file_path: str,
        content: str,
        language: str,
        chunk_size: int = 50,
    ) -> list[CodeChunk]:
        """Split a file into code chunks for indexing."""
        lines = content.split("\n")
        chunks: list[CodeChunk] = []

        for i in range(0, len(lines), chunk_size):
            end = min(i + chunk_size, len(lines))
            chunk_content = "\n".join(lines[i:end])

            # Extract symbols (simplified)
            symbols = self._extract_symbols(chunk_content, language)

            chunk = CodeChunk(
                chunk_id=f"{repo_id}:{file_path}:{i}-{end}",
                repo_id=repo_id,
                file_path=file_path,
                start_line=i + 1,
                end_line=end,
                content=chunk_content,
                language=language,
                symbols=symbols,
            )
            chunks.append(chunk)

        return chunks

    def _extract_symbols(self, content: str, language: str) -> list[str]:
        """Extract symbols (functions, classes) from code content."""
        import re

        symbols: list[str] = []

        if language == "python":
            # Match function and class definitions
            patterns = [
                r"def\s+(\w+)",
                r"class\s+(\w+)",
                r"async\s+def\s+(\w+)",
            ]
            for pattern in patterns:
                symbols.extend(re.findall(pattern, content))

        elif language in ("javascript", "typescript"):
            patterns = [
                r"function\s+(\w+)",
                r"class\s+(\w+)",
                r"const\s+(\w+)\s*=",
                r"export\s+(?:default\s+)?(?:function|class|const)\s+(\w+)",
            ]
            for pattern in patterns:
                symbols.extend(re.findall(pattern, content))

        elif language == "go":
            patterns = [
                r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)",
                r"type\s+(\w+)\s+struct",
                r"func\s+(\w+)\s*\(",
            ]
            for pattern in patterns:
                symbols.extend(re.findall(pattern, content))

        elif language == "rust":
            patterns = [
                r"fn\s+(\w+)",
                r"pub\s+fn\s+(\w+)",
                r"struct\s+(\w+)",
                r"impl\s+(\w+)",
            ]
            for pattern in patterns:
                symbols.extend(re.findall(pattern, content))

        return list(set(symbols))  # deduplicate
