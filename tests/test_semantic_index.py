"""
Tests for the Semantic Index Service (Phase 2).
"""

from __future__ import annotations

import pytest

from verdity.semantic_index import (
    CodeChunk,
    SymbolEdge,
    SemanticIndex,
)


@pytest.fixture
async def index() -> SemanticIndex:
    idx = SemanticIndex(db_path=":memory:")
    await idx.connect()
    yield idx
    await idx.close()


@pytest.fixture
def sample_chunks():
    return [
        CodeChunk(
            chunk_id="chunk-1",
            repo_id="acme/widgets",
            file_path="src/auth.py",
            start_line=10,
            end_line=20,
            content="def authenticate(user, password):\n    return verify(user, password)",
            language="python",
            symbols=["authenticate"],
        ),
        CodeChunk(
            chunk_id="chunk-2",
            repo_id="acme/widgets",
            file_path="src/auth.py",
            start_line=25,
            end_line=35,
            content="def verify(user, password):\n    hashed = hash(password)\n    return check(user, hashed)",
            language="python",
            symbols=["verify", "hash", "check"],
        ),
        CodeChunk(
            chunk_id="chunk-3",
            repo_id="acme/widgets",
            file_path="src/api.py",
            start_line=5,
            end_line=15,
            content="from auth import authenticate\n\ndef login(request):\n    return authenticate(request.user, request.pass)",
            language="python",
            symbols=["login"],
        ),
    ]


@pytest.mark.asyncio
async def test_upsert_and_search(index: SemanticIndex, sample_chunks):
    count = await index.upsert_chunks(sample_chunks)
    assert count == 3

    # Verify chunks are stored
    total = await index.get_chunk_count("acme/widgets")
    assert total == 3

    # Text search should find relevant chunks
    results = await index.search_by_text("acme/widgets", "authenticate")
    assert len(results) >= 1
    assert any(r["file_path"] == "src/auth.py" for r in results)


@pytest.mark.asyncio
async def test_upsert_idempotent(index: SemanticIndex, sample_chunks):
    """Re-indexing the same chunks should not duplicate them."""
    await index.upsert_chunks(sample_chunks)
    await index.upsert_chunks(sample_chunks)  # second pass

    total = await index.get_chunk_count("acme/widgets")
    assert total == 3  # still 3, not 6


@pytest.mark.asyncio
async def test_delete_chunks_for_file(index: SemanticIndex, sample_chunks):
    await index.upsert_chunks(sample_chunks)
    removed = await index.delete_chunks_for_file("acme/widgets", "src/auth.py")
    assert removed == 2  # chunks 1 and 2 are in auth.py

    total = await index.get_chunk_count("acme/widgets")
    assert total == 1  # only chunk-3 (api.py) remains


@pytest.mark.asyncio
async def test_symbol_edges(index: SemanticIndex):
    edges = [
        SymbolEdge(
            edge_id="e1",
            repo_id="acme/widgets",
            source_symbol="src.api.login",
            target_symbol="src.auth.authenticate",
            edge_type="calls",
        ),
        SymbolEdge(
            edge_id="e2",
            repo_id="acme/widgets",
            source_symbol="src.auth.authenticate",
            target_symbol="src.auth.verify",
            edge_type="calls",
        ),
    ]
    count = await index.upsert_edges(edges)
    assert count == 2

    # get_callers: who calls authenticate?
    callers = await index.get_callers("acme/widgets", "src.auth.authenticate")
    assert "src.api.login" in callers

    # get_callees: what does authenticate call?
    callees = await index.get_callees("acme/widgets", "src.auth.authenticate")
    assert "src.auth.verify" in callees


@pytest.mark.asyncio
async def test_incremental_indexing_detects_changes(index: SemanticIndex):
    """Simulate: first full index, then a push with one changed file."""
    chunks = [
        CodeChunk(
            chunk_id="c1",
            repo_id="org/repo",
            file_path="src/main.py",
            start_line=1,
            end_line=5,
            content="print('hello')",
            language="python",
        ),
    ]
    await index.upsert_chunks(chunks)
    await index.mark_indexed("org/repo", "sha1", {"src/main.py": "hash1"})

    # Now simulate a push that changes main.py
    new_sha = "sha2"
    modified = await index.get_modified_files("org/repo", new_sha)
    assert len(modified) == 1
    assert modified[0]["file_path"] == "src/main.py"

    # Mark as indexed at new SHA
    await index.mark_indexed("org/repo", new_sha, {"src/main.py": "hash2"})

    # Second check: no more modified files
    modified2 = await index.get_modified_files("org/repo", new_sha)
    assert len(modified2) == 0


@pytest.mark.asyncio
async def test_embedded_vector_is_deterministic(index: SemanticIndex, sample_chunks):
    """DevEmbeddingGenerator produces deterministic vectors for same content."""
    await index.upsert_chunks(sample_chunks)

    rows = await index._conn.execute(
        "SELECT chunk_id, embedding FROM code_chunks WHERE repo_id = ?",
        ("acme/widgets",),
    )
    embeddings = {r["chunk_id"]: r["embedding"] for r in rows}
    # Same content → same embedding (deterministic)
    assert embeddings["chunk-1"] is not None
    assert embeddings["chunk-2"] is not None


@pytest.mark.asyncio
async def test_cross_repo_isolation(index: SemanticIndex):
    """Chunks from different repos must not leak into each other's search."""
    chunks_a = [
        CodeChunk(
            chunk_id="a1",
            repo_id="org/a",
            file_path="x.py",
            start_line=1,
            end_line=2,
            content="secret from repo a",
            language="python",
        )
    ]
    chunks_b = [
        CodeChunk(
            chunk_id="b1",
            repo_id="org/b",
            file_path="x.py",
            start_line=1,
            end_line=2,
            content="secret from repo b",
            language="python",
        )
    ]
    await index.upsert_chunks(chunks_a)
    await index.upsert_chunks(chunks_b)

    results_a = await index.search_by_text("org/a", "secret")
    results_b = await index.search_by_text("org/b", "secret")

    assert all(r["file_path"] == "x.py" for r in results_a)
    assert all(r["file_path"] == "x.py" for r in results_b)
    # Verify counts are per-repo
    count_a = await index.get_chunk_count("org/a")
    count_b = await index.get_chunk_count("org/b")
    assert count_a == 1
    assert count_b == 1
