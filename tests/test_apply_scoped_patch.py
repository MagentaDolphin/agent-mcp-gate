import difflib
import stat

import pytest

from agent_mcp_gate.tools.apply_scoped_patch import apply_scoped_patch

_PATCH = "--- a/file.txt\n+++ b/file.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "file.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MCP_GATE_WORKSPACE", str(root))
    return root


def test_success_preserves_permissions_and_no_temporary_files(workspace):
    target = workspace / "file.txt"
    target.chmod(0o640)
    assert apply_scoped_patch("file.txt", _PATCH) == {
        "applied": True,
        "path": "file.txt",
        "hunks_applied": 1,
    }
    assert target.read_text() == "alpha\ngamma\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert sorted(item.name for item in workspace.iterdir()) == ["file.txt"]


@pytest.mark.parametrize("path", ["../outside.txt", "nested/../../outside.txt", ""])
def test_parent_components_and_empty_paths_are_out_of_scope(workspace, path):
    result = apply_scoped_patch(path, _PATCH)
    assert result["applied"] is False
    assert result["reason"] == "out_of_scope"
    assert (workspace / "file.txt").read_text() == "alpha\nbeta\n"


def test_absolute_path_even_inside_workspace_is_out_of_scope(workspace):
    result = apply_scoped_patch(str(workspace / "file.txt"), _PATCH)
    assert result["reason"] == "out_of_scope"


@pytest.mark.parametrize("directory", [False, True])
def test_symlink_escape_is_refused_without_touching_outside(workspace, directory):
    outside = workspace.parent / "outside"
    outside.mkdir()
    target = outside / "file.txt"
    target.write_text("alpha\nbeta\n")
    link = workspace / "link"
    link.symlink_to(outside if directory else target, target_is_directory=directory)
    result = apply_scoped_patch("link/file.txt" if directory else "link", _PATCH)
    assert result["reason"] == "symlink_escape"
    assert result["hunks_applied"] == 0
    assert target.read_text() == "alpha\nbeta\n"


def test_inside_symlink_is_allowed(workspace):
    (workspace / "alias").symlink_to(workspace / "file.txt")
    assert apply_scoped_patch("alias", _PATCH)["applied"] is True
    assert (workspace / "file.txt").read_text() == "alpha\ngamma\n"
    assert (workspace / "alias").is_symlink()


def test_nonmatching_patch_does_not_modify_file(workspace):
    result = apply_scoped_patch("file.txt", _PATCH.replace("-beta", "-other"))
    assert result["reason"] == "no_match" and result["hunks_applied"] == 0
    assert (workspace / "file.txt").read_text() == "alpha\nbeta\n"


def test_one_failed_hunk_rolls_back_all_hunks(workspace):
    patch = "@@ -1 +1 @@\n-alpha\n+changed\n@@ -2 +2 @@\n-other\n+new\n"
    result = apply_scoped_patch("file.txt", patch)
    assert result["reason"] == "no_match" and result["hunks_applied"] == 0
    assert (workspace / "file.txt").read_text() == "alpha\nbeta\n"


def test_multiple_hunks_match_exact_positions(workspace):
    old = "".join(f"line{i}\n" for i in range(15))
    new = old.replace("line1\n", "new1\ninserted\n").replace("line13\n", "new13\n")
    (workspace / "file.txt").write_text(old)
    patch = "".join(
        difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), n=1)
    )
    result = apply_scoped_patch("file.txt", patch)
    assert result["applied"] is True and result["hunks_applied"] == 2
    assert (workspace / "file.txt").read_text() == new


@pytest.mark.parametrize(
    "patch, expected",
    [
        ("@@ -0,0 +1 @@\n+before\n", "before\nalpha\nbeta\n"),
        ("@@ -1,2 +0,0 @@\n-alpha\n-beta\n", ""),
        ("@@ -2 +2 @@\n-beta\n+last\n\\ No newline at end of file\n", "alpha\nlast"),
    ],
)
def test_insert_delete_and_no_final_newline(workspace, patch, expected):
    assert apply_scoped_patch("file.txt", patch)["applied"] is True
    assert (workspace / "file.txt").read_text() == expected


@pytest.mark.parametrize(
    "patch, reason",
    [
        ("", "no_match"),
        ("not a diff", "invalid_patch"),
        ("@@ -1,3 +1 @@\n-alpha\n+changed\n", "invalid_patch"),
        ("@@ -0 +1 @@\n-alpha\n+changed\n", "invalid_patch"),
        ("@@ -1 +1 @@\n alpha\n", "no_match"),
    ],
)
def test_invalid_or_noop_patch_leaves_original(workspace, patch, reason):
    assert apply_scoped_patch("file.txt", patch)["reason"] == reason
    assert (workspace / "file.txt").read_text() == "alpha\nbeta\n"


def test_missing_workspace_is_fail_closed(monkeypatch):
    monkeypatch.delenv("AGENT_MCP_GATE_WORKSPACE", raising=False)
    assert apply_scoped_patch("file.txt", _PATCH)["reason"] == "workspace_not_configured"


def test_missing_file_directory_and_invalid_encoding(workspace):
    assert apply_scoped_patch("missing.txt", _PATCH)["reason"] == "not_found"
    assert apply_scoped_patch(".", _PATCH)["reason"] == "not_file"
    (workspace / "binary").write_bytes(b"\xff")
    assert apply_scoped_patch("binary", _PATCH)["reason"] == "invalid_encoding"
