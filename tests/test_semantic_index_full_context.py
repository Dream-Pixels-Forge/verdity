"""Tests for Semantic Index full-codebase context features (v0.3.0)."""
import pytest
from verdity.semantic_index import SemanticIndex, CodeChunk, SymbolEdge


class TestFullCodebaseContext:
    @pytest.mark.asyncio
    async def test_get_full_context(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        # Index some chunks
        chunks = [
            CodeChunk(
                chunk_id="repo:file1.py:0-10",
                repo_id="test/repo",
                file_path="file1.py",
                start_line=1,
                end_line=10,
                content="def foo(): pass",
                language="python",
                symbols=["foo"],
            ),
            CodeChunk(
                chunk_id="repo:file2.py:0-10",
                repo_id="test/repo",
                file_path="file2.py",
                start_line=1,
                end_line=10,
                content="def bar(): pass",
                language="python",
                symbols=["bar"],
            ),
        ]
        await index.upsert_chunks(chunks)

        # Add a call edge
        edges = [
            SymbolEdge(
                edge_id="edge1",
                repo_id="test/repo",
                source_symbol="foo",
                target_symbol="bar",
                edge_type="calls",
            )
        ]
        await index.upsert_edges(edges)

        # Get full context
        context = await index.get_full_context("test/repo", "file1.py")
        assert len(context) > 0
        assert any(c["file_path"] == "file1.py" for c in context)

        await index.close()

    @pytest.mark.asyncio
    async def test_get_file_dependencies(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        # Add import edges
        edges = [
            SymbolEdge(
                edge_id="edge1",
                repo_id="test/repo",
                source_symbol="main.py",
                target_symbol="utils.py",
                edge_type="imports",
            ),
            SymbolEdge(
                edge_id="edge2",
                repo_id="test/repo",
                source_symbol="test.py",
                target_symbol="main.py",
                edge_type="imports",
            ),
        ]
        await index.upsert_edges(edges)

        # Get dependencies
        deps = await index.get_file_dependencies("test/repo", "main.py")
        assert deps["file_path"] == "main.py"
        assert deps["import_count"] >= 0
        assert deps["depended_on_by_count"] >= 0

        await index.close()

    @pytest.mark.asyncio
    async def test_index_full_repo(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        files = [
            {"path": "main.py", "content": "def foo(): pass", "language": "python"},
            {"path": "utils.py", "content": "def bar(): pass", "language": "python"},
        ]

        result = await index.index_full_repo("test/repo", files, "abc123")
        assert result["repo_id"] == "test/repo"
        assert result["files_indexed"] == 2
        assert result["commit_sha"] == "abc123"

        # Verify chunks were created
        chunk_count = await index.get_chunk_count("test/repo")
        assert chunk_count > 0

        await index.close()

    @pytest.mark.asyncio
    async def test_chunk_file(self):
        index = SemanticIndex(db_path=":memory:")

        content = "line1\nline2\nline3\nline4\nline5"
        chunks = index._chunk_file(
            repo_id="test/repo",
            file_path="test.py",
            content=content,
            language="python",
            chunk_size=2,
        )

        assert len(chunks) == 3  # 5 lines / 2 = 2.5 -> 3 chunks
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 2
        assert chunks[1].start_line == 3
        assert chunks[1].end_line == 4
        assert chunks[2].start_line == 5
        assert chunks[2].end_line == 5

        await index.close()

    def test_extract_symbols_python(self):
        index = SemanticIndex(db_path=":memory:")

        content = """
def foo():
    pass

class Bar:
    def baz(self):
        pass

async def qux():
    pass
"""
        symbols = index._extract_symbols(content, "python")
        assert "foo" in symbols
        assert "Bar" in symbols
        assert "baz" in symbols
        assert "qux" in symbols

    def test_extract_symbols_javascript(self):
        index = SemanticIndex(db_path=":memory:")

        content = """
function foo() {}
class Bar {}
const baz = () => {};
export default function qux() {}
"""
        symbols = index._extract_symbols(content, "javascript")
        assert "foo" in symbols
        assert "Bar" in symbols
        assert "baz" in symbols
        assert "qux" in symbols

    def test_extract_symbols_go(self):
        index = SemanticIndex(db_path=":memory:")

        content = """
func Foo() {}
type Bar struct {}
func (b *Bar) Baz() {}
"""
        symbols = index._extract_symbols(content, "go")
        assert "Foo" in symbols
        assert "Bar" in symbols
        assert "Baz" in symbols

    def test_extract_symbols_rust(self):
        index = SemanticIndex(db_path=":memory:")

        content = """
fn foo() {}
pub fn bar() {}
struct Baz {}
impl Qux {}
"""
        symbols = index._extract_symbols(content, "rust")
        assert "foo" in symbols
        assert "bar" in symbols
        assert "Baz" in symbols
        assert "Qux" in symbols

    def test_extract_symbols_unknown_language(self):
        index = SemanticIndex(db_path=":memory:")
        symbols = index._extract_symbols("test", "unknown")
        assert symbols == []


class TestIncrementalReindexing:
    @pytest.mark.asyncio
    async def test_get_files_needing_reindex(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        # Add some files
        files = [
            {"path": "file1.py", "content": "content1", "language": "python"},
            {"path": "file2.py", "content": "content2", "language": "python"},
        ]
        await index.index_full_repo("test/repo", files)

        # Check which files need reindexing
        file_hashes = {
            "file1.py": "hash1",  # same
            "file2.py": "hash2_new",  # changed
            "file3.py": "hash3",  # new
        }

        needs_reindex = await index.get_files_needing_reindex("test/repo", file_hashes)
        assert "file2.py" in needs_reindex
        assert "file3.py" in needs_reindex
        assert "file1.py" not in needs_reindex

        await index.close()

    @pytest.mark.asyncio
    async def test_mark_file_indexed(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        await index.mark_file_indexed(
            "test/repo", "file.py", "hash123", "sha456"
        )

        # Verify metadata was stored
        rows = await index._conn.execute(
            "SELECT * FROM file_metadata WHERE repo_id = ? AND file_path = ?",
            ("test/repo", "file.py"),
        )
        assert len(rows) == 1
        assert rows[0]["content_hash"] == "hash123"
        assert rows[0]["last_indexed_sha"] == "sha456"

        await index.close()

    @pytest.mark.asyncio
    async def test_get_reindex_stats(self):
        index = SemanticIndex(db_path=":memory:")
        await index.connect()

        files = [
            {"path": "file1.py", "content": "content1", "language": "python"},
            {"path": "file2.py", "content": "content2", "language": "python"},
        ]
        await index.index_full_repo("test/repo", files)

        stats = await index.get_reindex_stats("test/repo")
        assert stats["repo_id"] == "test/repo"
        assert stats["total_files"] == 2
        assert stats["total_chunks"] > 0

        await index.close()
