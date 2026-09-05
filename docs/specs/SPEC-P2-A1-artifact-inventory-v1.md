# SPEC-P2-A1: Read-only artifact inventory v1

Status: locked implementation specification for P2-A1.

## Interface and safety boundary

`inventory_root(root, *, hash_paths=())` accepts an existing, canonical absolute
directory and returns metadata. It creates and modifies nothing. Traversal is
descriptor-relative and fail-closed unless `O_NOFOLLOW`, `O_DIRECTORY`, `dir_fd`
open, and fd-based `scandir` are available. Symlinks are reported but never
followed. Any root path component that is a symlink is refused.

`hash_paths` contains exact canonical POSIX-relative regular-file paths. Missing,
duplicate, absolute, dot-component, backslash, non-string, non-regular, symlink,
and H-025-protected targets are refused. File contents are read only for these
targets and hashed with SHA-256.

Names matching case-insensitive `H[-_ ]?0?25` token boundaries are protected.
A protected root or hash request is refused before probing. A protected child is
reported with `protection: H-025_UNRESOLVED`; protected directories are never
opened or traversed and protected files are never read.

Root, directory, and file identities are pinned and rechecked. Identity is the
tuple `(device, inode, type/mode, size, mtime_ns, ctime_ns)`. Any substitution or
concurrent mutation is refused with `InventoryRefused`.

## Output

The return value is:

```json
{"entries":[],"root":"/canonical/absolute/root","schema_version":"phase5.artifact_inventory.v1"}
```

Entries are ordered lexicographically by `relative_path`. Every entry contains
`relative_path`, `kind`, `artifact_type`, `engine`, `schema`, `research_stage`,
and `authority`. `engine`, `schema`, `research_stage`, and `authority` are always
`UNRESOLVED` in P2-A1. `artifact_type` is filename-metadata classification only:
`sqlite`, `json`, `jsonl`, `manifest`, `ledger`, or `unknown`. Manifest and ledger
name tokens take precedence over suffixes. Regular files also contain `size` and
authorized files contain lowercase `sha256`.

`canonical_json_bytes(inventory)` emits UTF-8 JSON using sorted keys, compact
separators, `ensure_ascii=False`, and no trailing newline. Non-finite numbers are
refused by the JSON encoder.
