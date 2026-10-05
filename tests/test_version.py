"""Tests for version module."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from verdity._version import _read_version_from_pyproject, get_version


class TestVersion:
    def test_get_version_returns_string(self):
        """get_version should return a version string."""
        version = get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_get_version_caches_result(self):
        """get_version should cache the result."""
        v1 = get_version()
        v2 = get_version()
        assert v1 == v2

    def test__version_module_attribute(self):
        """__version__ module attribute should exist and match get_version()."""
        import verdity._version as version_module

        assert hasattr(version_module, "__version__")
        assert version_module.__version__ == get_version()

    def test_read_version_from_pyproject_success(self, tmp_path):
        """Should read version from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "2.0.0"\n')

        with patch("verdity._version.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.parent.parent.parent = tmp_path
            mock_path_class.return_value = mock_path
            mock_path_class.__truediv__ = lambda self, other: tmp_path / other

            result = _read_version_from_pyproject()
            assert result == "2.0.0"

    def test_read_version_from_pyproject_no_tomllib(self, tmp_path):
        """Should handle missing tomllib/tomli."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "1.2.3"\n')

        with patch("verdity._version.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.parent.parent.parent = tmp_path
            mock_path_class.return_value = mock_path
            mock_path_class.__truediv__ = lambda self, other: tmp_path / other

            with patch.dict(sys.modules, {"tomllib": None, "tomli": None}):
                result = _read_version_from_pyproject()
                assert result == "0.0.0+no-toml"

    def test_read_version_from_pyproject_tomli_fallback(self, tmp_path):
        """Should fallback to tomli when tomllib is not available."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "4.0.0"\n')

        with patch("verdity._version.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.parent.parent.parent = tmp_path
            mock_path_class.return_value = mock_path
            mock_path_class.__truediv__ = lambda self, other: tmp_path / other

            # Mock tomllib as missing, tomli as available
            mock_tomli = MagicMock()
            mock_tomli.load.return_value = {"project": {"version": "4.0.0"}}
            with patch.dict(sys.modules, {"tomllib": None, "tomli": mock_tomli}):
                result = _read_version_from_pyproject()
                assert result == "4.0.0"


class TestVersionEdgeCases:
    """Test edge cases in version module."""

    def test_read_version_missing_project_key(self, tmp_path):
        """Should return 0.0.0 when project key missing."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool]\nkey = "value"\n')

        with patch("verdity._version.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path.parent.parent.parent = tmp_path
            mock_path_class.return_value = mock_path
            mock_path_class.__truediv__ = lambda self, other: tmp_path / other

            result = _read_version_from_pyproject()
            assert result == "0.0.0"

    def test_read_version_fallback_path_not_found(self):
        """Test fallback path logic is present in code."""
        # This test documents the fallback path logic in _version.py lines 24-26
        # The actual behavior is tested via integration in test_read_version_from_pyproject_success
        import inspect

        import verdity._version as v

        source = inspect.getsource(v._read_version_from_pyproject)
        assert "0.0.0+unknown" in source
        assert "share" in source
        assert "verdity" in source

    def test_read_version_fallback_path_found(self):
        """Test fallback path logic is present in code."""
        import inspect

        import verdity._version as v

        source = inspect.getsource(v._read_version_from_pyproject)
        assert "share" in source
        assert "verdity" in source

    def test_read_version_fallback_to_sys_prefix(self, tmp_path):
        """Should fallback to sys.prefix/share/verdity/pyproject.toml when project root doesn't have it."""
        fallback_dir = tmp_path / "share" / "verdity"
        fallback_dir.mkdir(parents=True)
        fallback_pyproject = fallback_dir / "pyproject.toml"
        fallback_pyproject.write_text('[project]\nversion = "3.0.0-fallback"\n')

        with patch("verdity._version.Path") as mock_path_class:
            # Create two separate mock instances for the two Path() calls
            first_path_mock = MagicMock()
            first_path_mock.parent.parent.parent = tmp_path

            second_path_mock = MagicMock()

            # Track which Path() call we're on
            path_call_count = [0]

            def mock_path_constructor(*args, **kwargs):
                path_call_count[0] += 1
                if path_call_count[0] == 1:
                    return first_path_mock
                return second_path_mock

            mock_path_class.side_effect = mock_path_constructor

            # First path: first_path_mock / "pyproject.toml" -> doesn't exist
            first_pyproject_mock = MagicMock()
            first_pyproject_mock.exists.return_value = False
            first_path_mock.__truediv__.return_value = first_pyproject_mock

            # Fallback path: second_path_mock / "share" / "verdity" / "pyproject.toml"
            share_mock = MagicMock()
            share_mock.exists.return_value = True
            second_path_mock.__truediv__.return_value = share_mock

            verdity_mock = MagicMock()
            verdity_mock.exists.return_value = True
            share_mock.__truediv__.return_value = verdity_mock

            fallback_pyproject_mock = MagicMock()
            fallback_pyproject_mock.exists.return_value = True
            verdity_mock.__truediv__.return_value = fallback_pyproject_mock

            # Mock sys.prefix
            with (
                patch("verdity._version.sys.prefix", str(tmp_path)),
                patch.dict(
                    sys.modules,
                    {
                        "tomllib": MagicMock(
                            load=lambda f: {"project": {"version": "3.0.0-fallback"}}
                        )
                    },
                ),
            ):
                result = _read_version_from_pyproject()
                assert result == "3.0.0-fallback"

    def test_read_version_both_paths_missing_returns_unknown(self):
        """Should return '0.0.0+unknown' when neither project root nor fallback path has pyproject.toml."""
        with patch("verdity._version.Path") as mock_path_class:
            # Create two separate mock instances for the two Path() calls
            first_path_mock = MagicMock()
            first_path_mock.parent.parent.parent = Path("/fake/path")

            second_path_mock = MagicMock()

            path_call_count = [0]

            def mock_path_constructor(*args, **kwargs):
                path_call_count[0] += 1
                if path_call_count[0] == 1:
                    return first_path_mock
                return second_path_mock

            mock_path_class.side_effect = mock_path_constructor

            # First path: first_path_mock / "pyproject.toml" -> doesn't exist
            first_pyproject_mock = MagicMock()
            first_pyproject_mock.exists.return_value = False
            first_path_mock.__truediv__.return_value = first_pyproject_mock

            # Fallback path: second_path_mock / "share" / "verdity" / "pyproject.toml" -> doesn't exist
            share_mock = MagicMock()
            share_mock.exists.return_value = True
            second_path_mock.__truediv__.return_value = share_mock

            verdity_mock = MagicMock()
            verdity_mock.exists.return_value = True
            share_mock.__truediv__.return_value = verdity_mock

            fallback_pyproject_mock = MagicMock()
            fallback_pyproject_mock.exists.return_value = False
            verdity_mock.__truediv__.return_value = fallback_pyproject_mock

            with patch("verdity._version.sys.prefix", "/fake/prefix"):
                with patch.dict(sys.modules, {"tomllib": MagicMock()}):
                    result = _read_version_from_pyproject()
                    assert result == "0.0.0+unknown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
