"""Bounded, resumable raw intake; never a research dataset or trading service."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from decimal import InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import time

from .realtime_probe import ENDPOINT, parse_ticker

SCHEMA = "mie.raw-intake.v1"
PRODUCT = "BTC-USD"
ROOT_FILE = "root.json"
CAPACITY_FILE = "capacity-v1.json"
CAPACITY_PARTIAL = "capacity-v1.partial"
FILE_PATTERN = re.compile(r"(\d{12})\.(jsonl|partial)")
MAX_FRAME = 1024 * 1024
RESERVE = 4096


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def line(record):
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n").encode("ascii")


def trade_diagnostics(payload, received, highwater):
    """Report possible duplicate/reordered IDs, not completeness or deduplication."""
    try:
        msg = json.loads(payload)
        if not isinstance(msg, dict) or msg.get("type") != "ticker":
            return highwater, 0, 0
        trade_id = parse_ticker(msg, PRODUCT, datetime.fromisoformat(received)).trade_id
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        return highwater, 0, 0
    if highwater is not None and trade_id <= highwater:
        return highwater, 1, 0
    return trade_id, 0, int(highwater is not None and trade_id > highwater + 1)


def sync_dir(root):
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def safe_root(path):
    root = Path(path)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("root must be an absolute canonical, non-symlink path")
    checkout = Path(__file__).resolve().parents[2]
    if root == checkout or checkout in root.parents or root in checkout.parents:
        raise ValueError("root must be separate from source checkout")
    return root


def init_root(path, owner, *, max_total_bytes=2_000_000_000):
    root = safe_root(path)
    if not owner.strip() or not 16_000_000 <= max_total_bytes <= 100_000_000_000:
        raise ValueError("invalid owner or capacity")
    root.mkdir(mode=0o700)  # exclusive; parent must already exist
    marker = {"schema": SCHEMA, "owner": owner, "root": str(root),
              "max_total_bytes": max_total_bytes, "evidence": False,
              "provider": "coinbase_exchange", "product": PRODUCT}
    with (root / ROOT_FILE).open("xb") as f:
        f.write(line(marker)); f.flush(); os.fsync(f.fileno())
    sync_dir(root)
    sync_dir(root.parent)
    return marker


def load_root(path):
    root = safe_root(path)
    file = root / ROOT_FILE
    if file.is_symlink() or not file.is_file():
        raise ValueError("explicit initialized root required")
    root_bytes = file.read_bytes()
    marker = json.loads(root_bytes)
    if (marker.get("schema") != SCHEMA or marker.get("root") != str(root)
            or marker.get("provider") != "coinbase_exchange"
            or marker.get("product") != PRODUCT or marker.get("evidence") is not False
            or not isinstance(marker.get("owner"), str) or not marker["owner"].strip()
            or type(marker.get("max_total_bytes")) is not int
            or not 16_000_000 <= marker["max_total_bytes"] <= 100_000_000_000):
        raise ValueError("invalid root marker")
    amendment_path = root / CAPACITY_FILE
    if amendment_path.is_symlink():
        raise ValueError("symlink capacity amendment")
    if amendment_path.exists():
        raw = amendment_path.read_bytes()
        amendment = json.loads(raw)
        if not isinstance(amendment, dict):
            raise ValueError("invalid capacity amendment")
        unsigned = {k: v for k, v in amendment.items() if k != "sha256"}
        expected = hashlib.sha256(line(unsigned)).hexdigest()
        if (raw != line(amendment) or amendment.get("schema") != "mie.raw-intake.capacity.v1"
                or amendment.get("sha256") != expected
                or amendment.get("root_marker_sha256") != hashlib.sha256(root_bytes).hexdigest()
                or amendment.get("previous_max_total_bytes") != marker["max_total_bytes"]
                or type(amendment.get("max_total_bytes")) is not int
                or not marker["max_total_bytes"] < amendment["max_total_bytes"] <= 100_000_000_000):
            raise ValueError("invalid capacity amendment")
        marker = {**marker, "max_total_bytes": amendment["max_total_bytes"]}
    return root, marker


def extend_capacity(path, *, expected_bytes, new_bytes):
    """Publish one operator-authorized increase; preserve root.json and segments."""
    root, _ = load_root(path)
    lock_fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root, marker = load_root(path)
        state = scan(root)
        final = root / CAPACITY_FILE
        if final.exists() or final.is_symlink():
            raise ValueError("capacity amendment already exists; no overwrite")
        if (type(expected_bytes) is not int or type(new_bytes) is not int
                or marker["max_total_bytes"] != expected_bytes
                or not expected_bytes < new_bytes <= 100_000_000_000):
            raise ValueError("capacity mismatch or invalid increase")
        if shutil.disk_usage(root).free < new_bytes - state["bytes"] + 50_000_000:
            raise ValueError("insufficient free space for requested capacity")
        amendment = {"schema": "mie.raw-intake.capacity.v1", "created_utc": utcnow(),
                     "root_marker_sha256": hashlib.sha256((root / ROOT_FILE).read_bytes()).hexdigest(),
                     "previous_max_total_bytes": expected_bytes, "max_total_bytes": new_bytes}
        amendment["sha256"] = hashlib.sha256(line(amendment)).hexdigest()
        partial = root / CAPACITY_PARTIAL
        with partial.open("xb") as f:
            f.write(line(amendment)); f.flush(); os.fsync(f.fileno())
        sync_dir(root)
        os.link(partial, final)
        sync_dir(root)
        partial.unlink()
        sync_dir(root)
        return amendment
    finally:
        os.close(lock_fd)


def verify_segment(path):
    digest = hashlib.sha256()
    header = footer = None
    count = 0
    last_received = None
    highwater = None
    duplicates = jumps = 0
    with path.open("rb") as f:
        while raw := f.readline(2 * MAX_FRAME + RESERVE + 1):
            if len(raw) > 2 * MAX_FRAME + RESERVE:
                raise ValueError("oversize record")
            item = json.loads(raw)
            if not isinstance(item, dict) or raw != line(item):
                raise ValueError("noncanonical record")
            if footer is not None:
                raise ValueError("records after footer")
            if header is None:
                if item.get("kind") != "header" or item.get("schema") != SCHEMA:
                    raise ValueError("missing header")
                header = item
                highwater = (header.get("previous") or {}).get("last_trade_id")
            elif item.get("kind") == "footer":
                footer = item
                digest.update(line({k: v for k, v in item.items() if k != "sha256"}))
                continue
            elif item.get("kind") == "message":
                count += 1
                if item.get("index") != count:
                    raise ValueError("message sequence mismatch")
                payload = base64.b64decode(item["raw_b64"], validate=True)
                last_received = item["received_utc"]
                highwater, dup, jump = trade_diagnostics(payload, last_received, highwater)
                duplicates += dup; jumps += jump
            elif item.get("kind") != "gap":
                raise ValueError("unknown record")
            digest.update(raw)
    if (header is None or footer is None or header.get("evidence") is not False
            or header.get("product") != PRODUCT or header.get("provider") != "coinbase_exchange"
            or footer.get("sha256") != digest.hexdigest() or footer.get("messages") != count
            or footer.get("last_received_utc") != last_received
            or footer.get("last_trade_id") != highwater
            or footer.get("duplicates_or_reordered") != duplicates
            or footer.get("trade_id_jumps") != jumps
            or footer.get("complete_trade_history") is not False):
        raise ValueError(f"invalid sealed segment: {path.name}")
    return header, footer


def scan(root):
    files = []
    partials = []
    total = 0
    last = None
    largest = 0
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unexpected root entry: {path.name}")
        total += path.stat().st_size
        if path.name in {ROOT_FILE, ".lock", CAPACITY_FILE}:
            continue
        match = FILE_PATTERN.fullmatch(path.name)
        if not match:
            raise ValueError(f"unexpected root entry: {path.name}")
        number = int(match[1]); largest = max(largest, number)
        if match[2] == "partial":
            partials.append(path.name)
            continue
        header, footer = verify_segment(path)
        if header.get("number") != number or header.get("previous") != last:
            raise ValueError("segment chain mismatch")
        last = {"number": number, "sha256": footer["sha256"],
                "last_received_utc": footer["last_received_utc"],
                "last_trade_id": footer["last_trade_id"],
                "ended_utc": footer["ended_utc"]}
        files.append(path.name)
    return dict(sealed=files, partials=partials, bytes=total, last=last, next_number=largest + 1)


def inspect_root(path):
    root, marker = load_root(path)
    return {**scan(root), "max_total_bytes": marker["max_total_bytes"]}


def collect_one(path, *, seconds=60, max_messages=10000, max_segment_bytes=8_000_000,
                connect=None, clock=utcnow, monotonic=time.monotonic):
    if not 1 <= seconds <= 300 or not 1 <= max_messages <= 10000:
        raise ValueError("capture bounds exceeded")
    if not 2 * MAX_FRAME <= max_segment_bytes <= 16_000_000:
        raise ValueError("segment capacity out of range")
    errors = (OSError, TimeoutError)
    if connect is None:
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect
        errors += (WebSocketException,)
    root, marker = load_root(path)
    # Mac/Linux advisory lock. Never follow an existing symlink.
    lock_fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root, marker = load_root(path)
        state = scan(root)
        budget = min(max_segment_bytes, marker["max_total_bytes"] - state["bytes"],
                     shutil.disk_usage(root).free - 50_000_000)
        if budget < 2 * MAX_FRAME:
            raise ValueError("capacity limit; no files deleted")
        number = state["next_number"]
        name = f"{number:012d}"
        partial, sealed = root / (name + ".partial"), root / (name + ".jsonl")
        digest = hashlib.sha256(); count = 0; last_received = None
        highwater = (state["last"] or {}).get("last_trade_id")
        duplicates = jumps = 0
        started = clock(); reason = "duration"
        with partial.open("xb") as f:
            def write(item, reserve=RESERVE):
                raw = line(item)
                if f.tell() + len(raw) + reserve > budget:
                    return False
                try:
                    f.write(raw); f.flush()
                except OSError as exc:
                    raise RuntimeError("storage failed; partial retained") from exc
                digest.update(raw)
                return True

            def write_gap(reason, **fields):
                # Data records reserve space for interruption records and the footer.
                if not write({"kind": "gap", "reason": reason, **fields}, reserve=1024):
                    raise RuntimeError("gap record exceeds budget; partial retained")

            def record_pause(previous, current, label):
                delta = (datetime.fromisoformat(current) - datetime.fromisoformat(previous)).total_seconds()
                if delta > 10 or delta < 0:
                    write_gap(label, previous_utc=previous, received_utc=current)
                    return True
                return False

            header = {"kind": "header", "schema": SCHEMA, "number": number,
                      "provider": "coinbase_exchange", "product": PRODUCT,
                      "started_utc": started, "previous": state["last"],
                      "unfinished_count": len(state["partials"]), "evidence": False,
                      "continuity": "UNKNOWN_AT_CONNECTION_BOUNDARY"}
            if not write(header):
                raise ValueError("header exceeds budget")
            if state["last"] is not None:
                record_pause(state["last"]["ended_utc"], started, "between_connections_pause_or_change")
            os.fsync(f.fileno()); sync_dir(root)
            previous_time = started
            pause_recorded = False
            try:
                with connect(ENDPOINT, open_timeout=10, close_timeout=2, max_size=MAX_FRAME) as ws:
                    ws.send(json.dumps({"type": "subscribe", "product_ids": [PRODUCT],
                                        "channels": ["ticker", "heartbeat"]}))
                    deadline = monotonic() + seconds
                    connected = clock()
                    pause_recorded = record_pause(previous_time, connected, "connection_start_pause_or_change")
                    previous_time = connected
                    while count < max_messages and monotonic() < deadline:
                        try:
                            raw = ws.recv(timeout=min(5, max(0.001, deadline - monotonic())))
                        except TimeoutError:
                            reason = "receive_timeout" if monotonic() < deadline else "duration"
                            break
                        received = clock()
                        gap = record_pause(previous_time, received, "wall_clock_pause_or_change")
                        previous_time = received
                        payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                        if len(payload) > MAX_FRAME:
                            reason = "oversize_frame"; break
                        if not write({"kind": "message", "index": count + 1,
                                      "received_utc": received, "frame": "text" if isinstance(raw, str) else "binary",
                                      "raw_b64": base64.b64encode(payload).decode("ascii")}):
                            reason = "segment_capacity"; break
                        count += 1; last_received = received
                        highwater, dup, jump = trade_diagnostics(payload, received, highwater)
                        duplicates += dup; jumps += jump
                        if gap:
                            pause_recorded = True
                            reason = "wall_clock_pause_or_change"
                            break
                    if count == max_messages and reason == "duration":
                        reason = "message_limit"
            except errors as exc:
                reason = "connection_failed:" + type(exc).__name__
            # A suspended receive can time out after the monotonic deadline, or
            # the loop can exit without receiving another frame after wake.
            # Compare wall-clock time at exit even when no message was received.
            ended = clock()
            if record_pause(previous_time, ended, "wall_clock_pause_or_change"):
                pause_recorded = True
                if reason in {"duration", "receive_timeout", "message_limit"}:
                    reason = "wall_clock_pause_or_change"
            if reason not in {"duration", "message_limit", "segment_capacity"} and not (
                reason == "wall_clock_pause_or_change" and pause_recorded
            ):
                write_gap(reason, utc=ended)
            footer = {"kind": "footer", "messages": count,
                      "last_received_utc": last_received, "ended_utc": ended,
                      "last_trade_id": highwater, "duplicates_or_reordered": duplicates,
                      "trade_id_jumps": jumps,
                      "reason": reason, "complete_trade_history": False}
            digest.update(line(footer))
            footer["sha256"] = digest.hexdigest()
            raw = line(footer)
            if f.tell() + len(raw) > budget:
                raise ValueError("footer exceeds budget")
            f.write(raw); f.flush(); os.fsync(f.fileno())
        verify_segment(partial)
        os.link(partial, sealed)  # exclusive publication, never overwrite
        sync_dir(root)
        partial.unlink()  # only our own published staging link
        sync_dir(root)
        return {"file": sealed.name, **footer}
    finally:
        os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init"); init.add_argument("--owner", required=True)
    init.add_argument("--max-total-bytes", type=int, default=2_000_000_000)
    sub.add_parser("verify")
    capacity = sub.add_parser("extend-capacity")
    capacity.add_argument("--expected-bytes", type=int, required=True)
    capacity.add_argument("--new-bytes", type=int, required=True)
    sub.add_parser("launchd-plist", help="print launch agent config; does not install or start it")
    collect = sub.add_parser("collect")
    collect.add_argument("--seconds", type=int, default=60)
    collect.add_argument("--segments", type=int, default=1)
    args = parser.parse_args(argv)
    if args.action == "init":
        print(json.dumps(init_root(args.root, args.owner, max_total_bytes=args.max_total_bytes)))
    elif args.action == "verify":
        print(json.dumps(inspect_root(args.root)))
    elif args.action == "extend-capacity":
        print(json.dumps(extend_capacity(args.root, expected_bytes=args.expected_bytes,
                                         new_bytes=args.new_bytes)))
    elif args.action == "launchd-plist":
        # Keep activation explicit. The emitted plist never invokes a shell.
        root, _ = load_root(args.root)
        plist = {"Label": "org.mie.realtime-collector-v1", "RunAtLoad": True,
                 "StartInterval": 60, "ProcessType": "Background",
                 "WorkingDirectory": str(Path(__file__).resolve().parents[2]),
                 "ProgramArguments": [sys.executable, "-m", "app.data.realtime_collector",
                                      "--root", str(root), "collect", "--seconds", "60",
                                      "--segments", "1440"]}
        sys.stdout.write(plistlib.dumps(plist).decode("utf-8"))
    else:
        if not 1 <= args.segments <= 1440:
            parser.error("segments must be between 1 and 1440")
        for i in range(args.segments):
            print(json.dumps(collect_one(args.root, seconds=args.seconds)), flush=True)
            if i + 1 < args.segments:
                time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
