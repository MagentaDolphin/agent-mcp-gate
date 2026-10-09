"""Scoped, all-or-nothing unified diff application using directory descriptors."""

import errno
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[^\r\n]*)\r?\n?$")


@dataclass
class _Hunk:
    old_start: int
    new_start: int
    old: list[str]
    new: list[str]


def _parse_patch(patch: str) -> list[_Hunk]:
    lines = patch.splitlines(keepends=True)
    index = 0
    if lines and lines[0].startswith("--- "):
        if len(lines) < 2 or not lines[1].startswith("+++ "):
            raise ValueError("Missing new-file header")
        index = 2
    hunks = []
    while index < len(lines):
        match = _HUNK.fullmatch(lines[index])
        if match is None:
            raise ValueError("Expected a unified-diff hunk")
        old_start, old_count, new_start, new_count = (
            int(value) if value is not None else 1 for value in match.groups()
        )
        if (old_count and old_start == 0) or (new_count and new_start == 0):
            raise ValueError("Nonempty ranges are one-based")
        index += 1
        old, new = [], []
        previous = None
        marked = False
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line.rstrip("\r\n") == "\\ No newline at end of file":
                if previous is None or marked:
                    raise ValueError("Invalid no-newline marker")
                if previous in (" ", "-"):
                    old[-1] = old[-1].removesuffix("\n").removesuffix("\r")
                if previous in (" ", "+"):
                    new[-1] = new[-1].removesuffix("\n").removesuffix("\r")
                marked = True
            elif line and line[0] in (" ", "+", "-"):
                previous, marked = line[0], False
                if previous in (" ", "-"):
                    old.append(line[1:])
                if previous in (" ", "+"):
                    new.append(line[1:])
            else:
                raise ValueError("Invalid hunk line")
            index += 1
        if len(old) != old_count or len(new) != new_count:
            raise ValueError("Hunk line counts do not match its header")
        if old_count == new_count == 0:
            raise ValueError("Empty hunk")
        hunks.append(_Hunk(old_start, new_start, old, new))
    return hunks


def _apply(original: str, hunks: list[_Hunk]) -> str | None:
    lines = original.splitlines(keepends=True)
    output = []
    cursor = 0
    for hunk in hunks:
        offset = hunk.old_start - 1 if hunk.old else hunk.old_start
        new_offset = hunk.new_start - 1 if hunk.new else hunk.new_start
        if offset < cursor or offset > len(lines):
            return None
        if lines[offset : offset + len(hunk.old)] != hunk.old:
            return None
        output.extend(lines[cursor:offset])
        if new_offset != len(output):
            return None
        output.extend(hunk.new)
        cursor = offset + len(hunk.old)
    output.extend(lines[cursor:])
    return "".join(output)


def apply_scoped_patch(path: str, patch: str) -> dict:
    """Apply to an existing UTF-8 file inside AGENT_MCP_GATE_WORKSPACE.

    Refuse absolute paths and any `..` component before resolving symlinks.
    Pin directories with O_NOFOLLOW and replace in the pinned parent directory.
    Never follow a symlink between the scope check and the write.
    """
    result = {"applied": False, "path": path, "hunks_applied": 0}

    def refuse(reason: str) -> dict:
        return {**result, "reason": reason}

    relative = Path(path)
    if not path or relative.is_absolute() or ".." in relative.parts:
        return refuse("out_of_scope")
    workspace = os.environ.get("AGENT_MCP_GATE_WORKSPACE")
    if not workspace:
        return refuse("workspace_not_configured")
    parent_fd = None
    temp_name = None
    try:
        root = Path(workspace).resolve(strict=True)
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            return refuse("symlink_escape")
        parts = target.relative_to(root).parts
        if not parts:
            return refuse("not_file")
        parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in parts[:-1]:
            child_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
            )
            os.close(parent_fd)
            parent_fd = child_fd
        # O_NONBLOCK ensures a FIFO cannot hang the tool before fstat rejects it.
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(file_fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                return refuse("not_file")
            original_bytes = stream.read()
        try:
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return refuse("invalid_encoding")
        try:
            hunks = _parse_patch(patch)
        except ValueError:
            return refuse("invalid_patch")
        if not hunks:
            return refuse("no_match")
        updated = _apply(original, hunks)
        if updated is None or updated == original:
            return refuse("no_match")
        temp_name = f".agent-mcp-patch-{secrets.token_hex(12)}"
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(temp_fd, "wb") as stream:
            stream.write(updated.encode("utf-8"))
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_name = None
        return {"applied": True, "path": path, "hunks_applied": len(hunks)}
    except FileNotFoundError:
        return refuse("not_found")
    except PermissionError:
        return refuse("permission_denied")
    except (ValueError, RuntimeError):
        return refuse("out_of_scope")
    except OSError as exc:
        return refuse("symlink_escape" if exc.errno == errno.ELOOP else "io_error")
    finally:
        if parent_fd is not None:
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except OSError:
                    pass
            os.close(parent_fd)
