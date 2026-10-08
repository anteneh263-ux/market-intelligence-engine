# WP6-2B — immutable local object publication v1

Implementation contract under ADR-MIE-004; synthetic development only. Owner is
app.research orchestration. This primitive does not allocate run reservations,
execute strategies, create evidence roots or grant runtime authorization. No old
stores are migrated; no equivalence with historical stores is asserted.

API: ObjectWriter(root, *, owner, run_id, max_bytes), put(bytes)->sha256,
close(), inspect_objects(root, *, header, objects)->report. Roots must already
exist, be absolute normalized paths with no symlink component. Refuse H-025-like
path components. Tests use newly allocated temporary directories only. Runtime
callers must separately establish approval for the exact root/owner/run; passing
these arguments is not proof of authorization.

Writer requires an empty directory. Create-exclusive `writer.json` is a permanent
claim, retained even after close or crash. Never reopen a claimed root for writing.
Header exact fields: schema_version=p6.object_store.v1, owner (WP0 token), run_id
(64 lowercase hex), max_bytes (positive signed64 integer). Canonical UTF8 JSON.
File `HASH.blob` contains the exact submitted bytes; no JSON/schema normalization.
max_bytes bounds unique blob bytes, excluding the small header. Empty bytes valid.
A repeated put with identical bytes returns the existing hash after checking the
stored bytes. No overwrite, repair, deletion or write retry after an uncertain result.

Open each directory component with O_DIRECTORY/O_NOFOLLOW and use root-descriptor
relative I/O. Bind root inode/device, check path identity before and after operations.
Writer owns a nonblocking exclusive advisory lock on the root directory while open;
inspection takes a shared nonblocking lock. Thus cooperative readers refuse active
writers. The permanent claim still forbids restart after the lock disappears.
Create files O_EXCL/O_NOFOLLOW, mode0600; write all bytes, fsync file, chmod0400,
fsync file again, fsync directory, then read back and verify. Return only after
verification and root-identity check. A failed publication permanently poisons that
writer and preserves partial files. Input errors before I/O are INPUT_INVALID;
storage/locking/root/publication failures STORAGE_REFUSED; subsequent poisoned,
closed, or fork-inherited writer operations WRITER_STOPPED. No process-fork reuse. Writer put/close are restricted to the creating thread;
foreign-thread calls refuse WRITER_STOPPED before I/O. A forked child may only
close its copied descriptor without explicit unlock, preserving the parent lock.
Do not claim power-loss safety from process-kill tests; fsync is requested, not
proof of hardware behavior. A malicious privileged writer is outside lock authority.

Inspector requires an explicit expected header and complete hash->byte-length map.
It takes no defaults from the directory and never creates/repairs anything. Validate
all names, regular files, single hard-link count, sizes, hashes and canonical header.
Reject unknown/partial/missing files, symlinks, directories and mutation detected
through before/after stat/name/root checks. Opening files uses O_NONBLOCK to avoid
FIFO hangs. No file is read beyond declared size+1; declared aggregate<=max_bytes.
Invalid expected arguments INPUT_INVALID; unsafe/unavailable root or lock
STORAGE_REFUSED; content/inventory mismatch INTEGRITY_FAILURE. Report exactly:
schema_version=p6.object_inspection.v1, structural_status=VALID,
technical_verification=LOCAL_ONLY, object_count, total_bytes,
runtime_accepted=false. Never VERIFIED or recovered/resumable. The map/header's
external authenticity is upstream; local inspection cannot prove an omitted tail
when both the tree and expected map were replaced. Read-only inspection may update
filesystem access times; it never writes evidence bytes.

Required tests: exact bytes/dedup/budget; absent/nonempty/relative/symlink/H025 roots;
concurrent writers and active inspection; post-close/restart/fork refusal; short
writes/fsync failures with terminal writer; partial files retained; content/size/
missing/extra/symlink/hardlink corruption; read-only verification; process crash
leaves permanent claim. This is a storage building block, not completion of WP6-2/3.

WP6-2C additive ownership API: `header` returns a detached canonical-header dict.
`claim()` on a fresh open same-process/thread writer with no blobs returns a unique
token once. Subsequent `put`/`close` require keyword `claim=token`; wrong tokens or
second claims refuse WRITER_OWNERSHIP before I/O without poisoning the owner.
Unclaimed writers keep their original API. This is cooperative in-process ownership,
not a sandbox against private Python attribute access. See durable-transitions-v1.
