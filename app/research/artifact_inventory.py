"""Fail-closed, read-only Phase 5 artifact inventory."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable

__all__ = ["InventoryRefused", "canonical_json_bytes", "inventory_root"]

_SCHEMA = "phase5.artifact_inventory.v1"
_H025 = re.compile(r"(?:^|[^a-z0-9])h[-_ ]?0?25(?:[^0-9]|$)", re.I)
_IDENTITY_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
_DIR_FD_OPEN_CAPABILITY = os.open
_FD_SCANDIR_CAPABILITY = os.scandir


class InventoryRefused(ValueError):
    """The requested inspection cannot be performed under the safety contract."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def inventory_root(root: str | os.PathLike[str], *, hash_paths: Iterable[str] = ()) -> dict:
    requested = _validate_hash_paths(hash_paths)
    raw = os.fspath(root)
    if not isinstance(raw, str):
        raise TypeError("inventory root must be a string path")
    if not os.path.isabs(raw) or os.path.normpath(raw) != raw:
        raise InventoryRefused("inventory root must be a canonical absolute path")
    if any(_is_h025(part) for part in Path(raw).parts):
        raise InventoryRefused("inventory root is H-025-protected")
    _require_capabilities()

    root_fd = _open_root(raw)
    try:
        root_identity = _identity(os.fstat(root_fd))
        identities: dict[str, tuple[int, ...]] = {}
        entries, regular_paths = _walk(root_fd, "", requested, identities, 0)
        _verify(root_fd, "", identities, 0)
        if _identity(os.fstat(root_fd)) != root_identity:
            raise InventoryRefused("inventory root changed during traversal")
    finally:
        os.close(root_fd)

    check_fd = _open_root(raw)
    try:
        if _identity(os.fstat(check_fd)) != root_identity:
            raise InventoryRefused("inventory root path changed during traversal")
    finally:
        os.close(check_fd)

    missing = requested - regular_paths
    if missing:
        raise InventoryRefused("hash target is missing or is not a regular file: " + ", ".join(sorted(missing)))
    return {"schema_version": _SCHEMA, "root": raw, "entries": sorted(entries, key=lambda e: e["relative_path"])}


def _validate_hash_paths(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("hash_paths must be an iterable of relative strings")
    try:
        items = tuple(values)
    except TypeError:
        raise TypeError("hash_paths must be an iterable of relative strings") from None
    result: list[str] = []
    for value in items:
        if not isinstance(value, str) or not value or "\\" in value:
            raise TypeError("hash_paths entries must be canonical relative strings")
        path = PurePosixPath(value)
        if path.is_absolute() or value != path.as_posix() or any(p in ("", ".", "..") for p in path.parts):
            raise ValueError("hash_paths entries must be canonical relative paths")
        if _is_h025(value):
            raise InventoryRefused("hash path is H-025-protected")
        result.append(value)
    if len(result) != len(set(result)):
        raise ValueError("hash_paths must not contain duplicates")
    return frozenset(result)


def _require_capabilities() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise InventoryRefused("required descriptor capability is unavailable")
    if _DIR_FD_OPEN_CAPABILITY not in getattr(os, "supports_dir_fd", set()):
        raise InventoryRefused("required dir_fd open capability is unavailable")
    if _FD_SCANDIR_CAPABILITY not in getattr(os, "supports_fd", set()):
        raise InventoryRefused("required fd scandir capability is unavailable")


def _open_root(path: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    try:
        fd = os.open(os.path.sep, flags)
        for component in Path(path).parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            try:
                os.close(fd)
            except OSError:
                try:
                    os.close(next_fd)
                finally:
                    next_fd = None
                raise
            fd = next_fd
        return fd
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if exc.errno == errno.ENOENT:
            raise InventoryRefused("inventory root does not exist") from None
        raise InventoryRefused("cannot open inventory root without symlinks") from None


def _identity(meta: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(meta, field) for field in _IDENTITY_FIELDS)


def _is_h025(name: str) -> bool:
    return _H025.search(name) is not None


def _base_entry(relative: str, kind: str) -> dict:
    return {
        "relative_path": relative,
        "kind": kind,
        "artifact_type": _classify(relative),
        "engine": "UNRESOLVED",
        "schema": "UNRESOLVED",
        "research_stage": "UNRESOLVED",
        "authority": "UNRESOLVED",
    }


def _classify(relative: str) -> str:
    name = PurePosixPath(relative).name.lower()
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", name)))
    if "manifest" in tokens:
        return "manifest"
    if "ledger" in tokens:
        return "ledger"
    if name.endswith((".sqlite", ".sqlite3", ".db")):
        return "sqlite"
    if name.endswith(".jsonl"):
        return "jsonl"
    if name.endswith(".json"):
        return "json"
    return "unknown"


def _snapshot(fd: int) -> list[tuple[str, os.stat_result]]:
    try:
        with os.scandir(fd) as iterator:
            children = sorted(iterator, key=lambda child: child.name)
        return [(child.name, child.stat(follow_symlinks=False)) for child in children]
    except OSError as exc:
        raise InventoryRefused("cannot inspect inventory directory") from None


def _walk(fd: int, prefix: str, requested: frozenset[str], identities: dict[str, tuple[int, ...]], depth: int):
    if depth > 128:
        raise InventoryRefused("inventory directory depth limit exceeded")
    before = _snapshot(fd)
    entries: list[dict] = []
    regular: set[str] = set()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    for name, meta in before:
        relative = f"{prefix}/{name}" if prefix else name
        identities[relative] = _identity(meta)
        protected = _is_h025(relative)
        if stat.S_ISLNK(meta.st_mode):
            entry = _base_entry(relative, "symlink")
        elif stat.S_ISDIR(meta.st_mode):
            entry = _base_entry(relative, "directory")
            if not protected:
                try:
                    child_fd = os.open(name, directory_flags, dir_fd=fd)
                except OSError:
                    raise InventoryRefused("cannot pin inventory directory") from None
                try:
                    if _identity(os.fstat(child_fd)) != _identity(meta):
                        raise InventoryRefused("inventory directory changed before traversal")
                    descendants, child_regular = _walk(child_fd, relative, requested, identities, depth + 1)
                    entries.extend(descendants)
                    regular.update(child_regular)
                finally:
                    os.close(child_fd)
        elif stat.S_ISREG(meta.st_mode):
            regular.add(relative)
            entry = _base_entry(relative, "regular_file")
            entry["size"] = meta.st_size
            if relative in requested and not protected:
                entry["sha256"] = _hash_file(fd, name, meta)
        else:
            entry = _base_entry(relative, "special")
        if protected:
            entry["protection"] = "H-025_UNRESOLVED"
        entries.append(entry)
    if _snapshot_key(before) != _snapshot_key(_snapshot(fd)):
        raise InventoryRefused("inventory directory changed during traversal")
    return entries, regular


def _snapshot_key(snapshot):
    return tuple((name, _identity(meta)) for name, meta in snapshot)


def _verify(fd: int, prefix: str, identities: dict[str, tuple[int, ...]], depth: int) -> None:
    if depth > 128:
        raise InventoryRefused("inventory directory depth limit exceeded")
    before = _snapshot(fd)
    direct_expected = {p for p in identities if p.rpartition("/")[0] == prefix}
    direct_actual = {f"{prefix}/{n}" if prefix else n for n, _ in before}
    if direct_actual != direct_expected:
        raise InventoryRefused("inventory tree changed before verification")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    for name, meta in before:
        relative = f"{prefix}/{name}" if prefix else name
        if _identity(meta) != identities[relative]:
            raise InventoryRefused("inventory entry changed before verification")
        if stat.S_ISDIR(meta.st_mode) and not _is_h025(relative):
            try:
                child_fd = os.open(name, flags, dir_fd=fd)
            except OSError:
                raise InventoryRefused("inventory directory changed before verification") from None
            try:
                if _identity(os.fstat(child_fd)) != identities[relative]:
                    raise InventoryRefused("inventory directory changed before verification")
                _verify(child_fd, relative, identities, depth + 1)
            finally:
                os.close(child_fd)
    if _snapshot_key(before) != _snapshot_key(_snapshot(fd)):
        raise InventoryRefused("inventory directory changed during verification")


def _hash_file(parent_fd: int, name: str, observed: os.stat_result) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError:
        raise InventoryRefused("cannot open authorized regular file") from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(observed):
            raise InventoryRefused("authorized file changed before hashing")
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        if _identity(os.fstat(fd)) != _identity(before):
            raise InventoryRefused("authorized file changed while hashing")
        return digest.hexdigest()
    finally:
        os.close(fd)
