"""Mount-source validation. String prefix is not a security boundary."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ai_lab.sandbox.errors import SandboxSecurityPolicyError


# Символы, которые ломают Docker `--mount type=bind,source=...,target=...`.
_MOUNT_UNSAFE_CHARS = frozenset({",", "\n", "\r", "\0", "=", "*", ";", "|", "&", "`"})


def is_path_inside(child: Path, parent: Path) -> bool:
    """True iff resolved `child` is `parent` or a descendant.

    Uses Path.relative_to after resolve(), not str.startswith().
    `C:\\project2` is not inside `C:\\project`. `..` and symlinks/junctions
    that escape the root fail.
    """
    try:
        child_r = Path(child).resolve(strict=False)
        parent_r = Path(parent).resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if _is_unc(child_r) and not _is_unc(parent_r):
        return False
    try:
        child_r.relative_to(parent_r)
        return True
    except ValueError:
        if sys.platform != "win32":
            return False
        # Windows paths are case-insensitive; relative_to is not always.
        try:
            Path(os.path.normcase(str(child_r))).relative_to(Path(os.path.normcase(str(parent_r))))
            return True
        except ValueError:
            return False


def validate_bind_source(source: Path, allowed_root: Path) -> Path:
    """Нормализовать host path и убедиться, что он внутри run-scoped root.

    Недостаточно проверить string prefix: `C:\\project2` начинается с
    `C:\\project`, но это другой каталог.
    """
    if source is None or allowed_root is None:
        raise SandboxSecurityPolicyError("mount source and allowed root are required")
    src = Path(source)
    root = Path(allowed_root)
    if _contains_nul(src) or _contains_nul(root):
        raise SandboxSecurityPolicyError("mount path contains NUL")
    try:
        resolved_src = src.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SandboxSecurityPolicyError(f"cannot resolve mount path: {exc}") from exc
    if not is_path_inside(resolved_src, resolved_root):
        raise SandboxSecurityPolicyError(
            f"mount source {resolved_src} is outside allowed root {resolved_root}"
        )
    rendered = str(resolved_src)
    if any(c in rendered for c in _MOUNT_UNSAFE_CHARS):
        raise SandboxSecurityPolicyError("mount source contains characters unsafe for Docker --mount")
    return resolved_src


def validate_runner_source(runner: Path, *, expected: Path) -> Path:
    """Runner монтируется read-only и должен быть нашим trusted file, не user path."""
    resolved = Path(runner).resolve(strict=False)
    expected_r = Path(expected).resolve(strict=False)
    if not resolved.is_file():
        raise SandboxSecurityPolicyError(f"sandbox runner missing: {resolved}")
    if not _same_file(resolved, expected_r):
        raise SandboxSecurityPolicyError("sandbox runner path is not the trusted package runner")
    if any(c in str(resolved) for c in _MOUNT_UNSAFE_CHARS):
        raise SandboxSecurityPolicyError("runner path contains characters unsafe for Docker --mount")
    return resolved


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _is_unc(path: Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def _contains_nul(path: Path) -> bool:
    return "\0" in str(path)
