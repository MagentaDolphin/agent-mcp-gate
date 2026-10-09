"""Validated, explicit filesystem and resource policy for sandbox execution."""

import json
import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_READ_ONLY_ROOTS = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives")
DEFAULT_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "HOME", "PYTHONPATH")


class SandboxPolicyError(ValueError):
    """A policy cannot be read or cannot safely describe the requested execution."""


class SandboxUnavailable(RuntimeError):
    """The required bubblewrap executable cannot be found or started."""


def _string_list(value, name):
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise SandboxPolicyError(f"{name} must be a list of strings")
    if any(not v or "\0" in v for v in value) or len(set(value)) != len(value):
        raise SandboxPolicyError(f"{name} must contain distinct nonempty strings without NUL")
    return tuple(value)


@dataclass(frozen=True)
class SandboxPolicy:
    workspace: str
    read_only_roots: tuple[str, ...] = field(default_factory=lambda: DEFAULT_READ_ONLY_ROOTS)
    env_allowlist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_ENV_ALLOWLIST)
    network: bool = False
    timeout_s: float = 60
    max_output_bytes: int = 1024 * 1024
    tmpfs_size: int = 64 * 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.workspace, (str, Path)) or not str(self.workspace):
            raise SandboxPolicyError("workspace is required and must be an absolute directory")
        try:
            workspace = Path(self.workspace)
            if not workspace.is_absolute() or not workspace.is_dir():
                raise SandboxPolicyError("workspace must be an absolute existing directory")
            workspace = workspace.resolve(strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise SandboxPolicyError(f"invalid workspace: {exc}") from exc
        if workspace in (Path("/"), Path("/tmp")):
            raise SandboxPolicyError("workspace cannot replace the sandbox root or private /tmp")
        roots = _string_list(self.read_only_roots, "read_only_roots")
        for root in roots:
            try:
                path = Path(root)
                if not path.is_absolute() or path == Path("/"):
                    raise SandboxPolicyError("read_only_roots must be absolute paths other than /")
                # Some distributions have no /lib64. Missing standard roots are harmless;
                # explicit nonstandard roots must exist so a typo cannot silently lose a bind.
                if not path.exists():
                    if root in DEFAULT_READ_ONLY_ROOTS:
                        continue
                    raise SandboxPolicyError(f"read-only root does not exist: {root}")
                resolved = path.resolve(strict=True)
                if workspace.is_relative_to(resolved) or resolved.is_relative_to(workspace):
                    raise SandboxPolicyError(f"read-only root overlaps workspace: {root}")
                if path == Path("/tmp"):
                    raise SandboxPolicyError("read-only roots cannot replace private /tmp")
            except (OSError, ValueError, RuntimeError) as exc:
                raise SandboxPolicyError(f"invalid read-only root {root}: {exc}") from exc
        env = _string_list(self.env_allowlist, "env_allowlist")
        if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None for name in env):
            raise SandboxPolicyError("env_allowlist contains an invalid variable name")
        if not isinstance(self.network, bool):
            raise SandboxPolicyError("network must be a boolean")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise SandboxPolicyError("timeout_s must be a finite positive number")
        for name, value, minimum in [
            ("max_output_bytes", self.max_output_bytes, 0),
            ("tmpfs_size", self.tmpfs_size, 1),
        ]:
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise SandboxPolicyError(f"{name} must be an integer >= {minimum}")
        object.__setattr__(self, "workspace", str(workspace))
        object.__setattr__(self, "read_only_roots", roots)
        object.__setattr__(self, "env_allowlist", env)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise SandboxPolicyError("policy must be a JSON object")
        if "workspace" not in data:
            raise SandboxPolicyError("workspace is required")
        unknown = set(data) - {item.name for item in fields(cls)}
        if unknown:
            raise SandboxPolicyError(f"unknown policy fields: {sorted(unknown, key=str)}")
        try:
            return cls(**data)
        except TypeError as exc:
            raise SandboxPolicyError(f"invalid policy: {exc}") from exc

    @classmethod
    def from_json(cls, path):
        """Read a JSON policy file; parse and I/O errors never permit execution."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise SandboxPolicyError(f"cannot read sandbox policy {path}: {exc}") from exc
        return cls.from_dict(data)

    def snapshot(self):
        return {
            "workspace": self.workspace,
            "read_only_roots": list(self.read_only_roots),
            "env_allowlist": list(self.env_allowlist),
            "network": self.network,
            "timeout_s": self.timeout_s,
            "max_output_bytes": self.max_output_bytes,
            "tmpfs_size": self.tmpfs_size,
        }
