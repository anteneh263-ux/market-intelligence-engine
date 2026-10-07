from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest


def api():
    from app.research.artifact_inventory import (
        InventoryRefused,
        canonical_json_bytes,
        inventory_root,
    )
    return InventoryRefused, canonical_json_bytes, inventory_root


def test_rejects_relative_missing_and_noncanonical_roots_without_creation(tmp_path):
    refused, _, inventory = api()
    for root in ("missing/root", tmp_path / "missing" / "root", str(tmp_path) + "/."):
        with pytest.raises(refused):
            inventory(root)
    assert not (tmp_path / "missing").exists()


def test_rejects_root_symlink_and_symlink_component(tmp_path):
    refused, _, inventory = api()
    real = tmp_path / "real" / "root"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)
    for root in (tmp_path / "root-link", link / "root"):
        if root.name == "root-link":
            root.symlink_to(real, target_is_directory=True)
        with pytest.raises(refused):
            inventory(root)


def test_requires_descriptor_capabilities(monkeypatch):
    import app.research.artifact_inventory as m
    monkeypatch.delattr(m.os, "O_NOFOLLOW")
    with pytest.raises(m.InventoryRefused, match="capability"):
        m.inventory_root("/not-opened")


def test_metadata_only_tree_is_sorted_and_symlinks_are_not_followed(tmp_path):
    _, _, inventory = api()
    root = tmp_path / "evidence"
    (root / "z").mkdir(parents=True)
    (root / "z" / "data.json").write_bytes(b"secret")
    (root / "a.sqlite").write_bytes(b"sqlite")
    (root / "link").symlink_to(root / "z", target_is_directory=True)
    result = inventory(root)
    assert [e["relative_path"] for e in result["entries"]] == ["a.sqlite", "link", "z", "z/data.json"]
    assert all("sha256" not in e for e in result["entries"])
    assert next(e for e in result["entries"] if e["relative_path"] == "link")["kind"] == "symlink"


@pytest.mark.parametrize("bad", ["x", ["../x"], ["/x"], ["a/../x"], ["a\\x"], ["x", "x"], [1]])
def test_rejects_invalid_duplicate_or_noncanonical_hash_targets(tmp_path, bad):
    refused, _, inventory = api()
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises((TypeError, ValueError, refused)):
        inventory(root, hash_paths=bad)


def test_rejects_missing_symlink_directory_and_special_hash_targets(tmp_path):
    refused, _, inventory = api()
    root = tmp_path / "root"
    root.mkdir()
    (root / "directory").mkdir()
    (root / "link").symlink_to(root / "directory", target_is_directory=True)
    for target in ("missing", "directory", "link"):
        with pytest.raises(refused):
            inventory(root, hash_paths=(target,))


def test_hashes_only_exact_authorized_regular_file(tmp_path):
    _, _, inventory = api()
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.json").write_bytes(b"one")
    (root / "two.json").write_bytes(b"two")
    entries = inventory(root, hash_paths=("one.json",))["entries"]
    assert entries[0]["sha256"] == hashlib.sha256(b"one").hexdigest()
    assert "sha256" not in entries[1]


@pytest.mark.parametrize("name", ["H-025", "h_025-review", "H025-data"])
def test_never_opens_or_reads_h025_named_descendants(tmp_path, monkeypatch, name):
    import app.research.artifact_inventory as m
    root = tmp_path / "root"
    protected = root / name
    protected.mkdir(parents=True)
    (protected / "secret.json").write_bytes(b"secret")
    real_open = m.os.open
    opened = []
    def spy(path, *args, **kwargs):
        opened.append(os.fspath(path))
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(m.os, "open", spy)
    result = m.inventory_root(root)
    assert name not in opened
    assert result["entries"][0]["protection"] == "H-025_UNRESOLVED"
    with pytest.raises(m.InventoryRefused, match="H-025"):
        m.inventory_root(root, hash_paths=(f"{name}/secret.json",))


def test_h025_root_is_rejected_before_open(monkeypatch):
    import app.research.artifact_inventory as m
    monkeypatch.setattr(m.os, "open", lambda *a, **k: pytest.fail("must not probe"))
    with pytest.raises(m.InventoryRefused, match="H-025"):
        m.inventory_root("/safe/H-025/evidence")


@pytest.mark.parametrize("name,expected", [
    ("db.sqlite3", "sqlite"), ("data.json", "json"), ("events.jsonl", "jsonl"),
    ("run-manifest.json", "manifest"), ("audit_ledger.db", "ledger"), ("blob.bin", "unknown")])
def test_classification_is_metadata_only_and_authority_unresolved(tmp_path, name, expected):
    _, _, inventory = api()
    root = tmp_path / "root"
    root.mkdir()
    (root / name).write_bytes(b"x")
    entry = inventory(root)["entries"][0]
    assert entry["artifact_type"] == expected
    assert {entry[k] for k in ("engine", "schema", "research_stage", "authority")} == {"UNRESOLVED"}


def test_canonical_json_bytes_are_deterministic():
    _, canonical, _ = api()
    value = {"z": "æ", "a": [2, 1]}
    expected = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert canonical(value) == canonical(value) == expected


def test_detects_file_mutation_during_hash(tmp_path, monkeypatch):
    import app.research.artifact_inventory as m
    root = tmp_path / "root"
    root.mkdir()
    target = root / "x.json"
    target.write_bytes(b"A" * 4096)
    real_read = m.os.read
    changed = False
    def racing_read(fd, size):
        nonlocal changed
        chunk = real_read(fd, size)
        if chunk and not changed:
            changed = True
            target.write_bytes(b"B" * 4097)
        return chunk
    monkeypatch.setattr(m.os, "read", racing_read)
    with pytest.raises(m.InventoryRefused, match="changed"):
        m.inventory_root(root, hash_paths=("x.json",))


def test_detects_root_substitution(tmp_path, monkeypatch):
    import app.research.artifact_inventory as m
    root = tmp_path / "root"
    root.mkdir()
    (root / "safe.json").write_bytes(b"safe")
    moved = tmp_path / "moved"
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    real_scandir = m.os.scandir
    done = False
    def racing_scandir(fd):
        nonlocal done
        iterator = real_scandir(fd)
        if not done:
            done = True
            root.rename(moved)
            root.symlink_to(attacker, target_is_directory=True)
        return iterator
    monkeypatch.setattr(m.os, "scandir", racing_scandir)
    with pytest.raises(m.InventoryRefused):
        m.inventory_root(root)


def test_root_handoff_closes_new_descriptor_if_old_close_fails(monkeypatch):
    import app.research.artifact_inventory as m
    descriptors = iter((100, 101))
    closed = []
    monkeypatch.setattr(m.os, "open", lambda *a, **k: next(descriptors))
    def failing_close(fd):
        closed.append(fd)
        if fd == 100 and closed.count(100) == 1:
            raise OSError("injected close failure")
    monkeypatch.setattr(m.os, "close", failing_close)
    with pytest.raises(m.InventoryRefused):
        m._open_root("/safe")
    assert 101 in closed


def test_detects_nested_directory_substitution(tmp_path, monkeypatch):
    import app.research.artifact_inventory as m
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.json").write_bytes(b"safe")
    moved = root / "moved"
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    real_open = m.os.open
    swapped = False
    def racing_open(path, *args, **kwargs):
        nonlocal swapped
        if os.fspath(path) == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            nested.rename(moved)
            nested.symlink_to(attacker, target_is_directory=True)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(m.os, "open", racing_open)
    with pytest.raises(m.InventoryRefused):
        m.inventory_root(root)


def test_detects_regular_file_inode_substitution(tmp_path, monkeypatch):
    import app.research.artifact_inventory as m
    root = tmp_path / "root"
    root.mkdir()
    target = root / "x.json"
    target.write_bytes(b"safe")
    original = root / "original.json"
    real_open = m.os.open
    swapped = False
    def racing_open(path, *args, **kwargs):
        nonlocal swapped
        if os.fspath(path) == "x.json" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            target.rename(original)
            target.write_bytes(b"evil")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(m.os, "open", racing_open)
    with pytest.raises(m.InventoryRefused, match="changed"):
        m.inventory_root(root, hash_paths=("x.json",))
