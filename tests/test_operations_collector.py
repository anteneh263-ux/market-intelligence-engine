"""RED test suite — MIE Bitstamp evidence collector.

Encodes the consolidated COLLECTOR_EVIDENCE_CONTRACT R2 + R3 + R4 (+ the three
reviewer clarifications accompanying the RED-implementation approval).

STATUS: RED. ``app.operations.collector`` does not exist yet. Every test below
must FAIL (fixture ERROR: ModuleNotFoundError) until the GREEN phase implements
the contract. No test may pass before then.

Pinned GREEN API surface (tests are the contract boundary — no deeper mocking):
    collector.main(argv: list[str], *, transport=None, now_utc: str | None = None,
                   _fault: str | None = None) -> int          # closed exit codes 0..6
    collector.InjectedFault                                   # raised at _fault points
    transport.get(url, headers=..., timeout=...) -> resp      # the ONLY network seam
        resp.status: int; resp.headers: dict; resp.chunks: iterable[bytes]
        transport may raise TimeoutError / ConnectionError
``now_utc`` and ``_fault`` are deterministic test seams (precedent:
_InjectedFault in app/paper/session/reporting.py). Production runs omit both.

Fault tokens (exact, closed):
  after_run_start, after_archive_publish, after_index_append, after_candle_append,
  after_run_end, reconA_after_run_start, reconA_after_incident, reconA_after_run_end,
  reconA_after_frozen, archive_fsync, candle_fsync, archive_rename

No network. No production code. Fixtures build evidence state on disk exactly per
the contract schemas; helpers reimplement canonical JSON + sha256 with stdlib only
(identical semantics to app/forward/manifest.py) so collection never imports app.*.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# constants                                                                    #
# --------------------------------------------------------------------------- #
DAY = 86400
CID = "h025-forward-20260801"          # live cycle (test_start 2026-08-01)
REH = "rehearsal-20260725"             # rehearsal cycle
NOW = "2026-08-03T05:00:00Z"           # default deterministic clock
GENESIS = "0" * 64

EXIT_OK, EXIT_DEGRADED, EXIT_FAILED, EXIT_FROZEN = 0, 1, 2, 3
EXIT_LOCK_HELD, EXIT_USAGE, EXIT_CORRUPTION = 4, 5, 6

PERF_TOKENS = ("pnl", "p&l", "sharpe", "drawdown", "signal", "position",
               "equity", "return", "benchmark", "buy_and_hold", "order",
               "profit", "loss")

# --------------------------------------------------------------------------- #
# stdlib canonical helpers (same semantics as app/forward/manifest.py)         #
# --------------------------------------------------------------------------- #
def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def day_ts(day: str) -> int:
    """UTC midnight epoch-seconds for 'YYYY-MM-DD'."""
    import datetime as dt
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def C(day: str, o="100.00", h="110.00", l="90.00", c="105.00", v="10.00") -> dict:
    """One provider-format candle (values as decimal strings, per contract)."""
    return {"timestamp": str(day_ts(day)), "open": o, "high": h, "low": l,
            "close": c, "volume": v}


def body(*candles: dict) -> bytes:
    return json.dumps({"data": {"pair": "BTC/USD", "ohlc": list(candles)}}).encode()


# --------------------------------------------------------------------------- #
# transport fake (the contract seam — nothing deeper is mocked)                #
# --------------------------------------------------------------------------- #
class Resp:
    def __init__(self, status=200, content_type="application/json",
                 content_encoding=None, payload=b"", chunk=65536):
        self.status = status
        self.headers = {"Content-Type": content_type}
        if content_encoding:
            self.headers["Content-Encoding"] = content_encoding
        self._payload = payload
        self._chunk = chunk

    @property
    def chunks(self):
        p, n = self._payload, self._chunk
        return [p[i:i + n] for i in range(0, len(p), n)] or [b""]


class Transport:
    """Queue of responses/exceptions; records every request made."""
    def __init__(self, *items):
        self.queue = list(items)
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}),
                           "timeout": timeout})
        if not self.queue:
            raise AssertionError("transport called more times than scripted")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def T(*payloads: bytes) -> Transport:
    return Transport(*[Resp(payload=p) for p in payloads])


# --------------------------------------------------------------------------- #
# evidence-state builder (writes contract-schema state directly on disk)       #
# --------------------------------------------------------------------------- #
class Cycle:
    def __init__(self, root: Path, cid: str = CID):
        self.cid = cid
        self.dir = root / "cycles" / cid
        self.col = self.dir / "collector"
        for d in ("raw/.staging", "incidents", "emergency"):
            (self.col / d).mkdir(parents=True, exist_ok=True)
        (self.dir / "baseline").mkdir(parents=True, exist_ok=True)
        (self.dir / "quarantine").mkdir(parents=True, exist_ok=True)
        self._idx_parent = GENESIS
        self._run_parent = GENESIS
        self._cand_parent = None       # set to baseline_sha256 by seed_baseline
        self.baseline_sha = None

    # ---- chained appends (schema-faithful) -------------------------------- #
    def _append(self, path: Path, record: dict, parent: str) -> str:
        rec = dict(record)
        rec["previous_record_hash"] = parent
        h = sha(canon(rec))
        rec["record_hash"] = h
        with open(path, "a", encoding="utf-8") as f:
            f.write(canon(rec) + "\n")
        return h

    def seed_baseline(self, days: list[str]):
        p = self.dir / "baseline" / "baseline_candles.jsonl"
        parent = GENESIS
        for d in days:
            parent = self._append(p, self._cand_record(d, C(d), raw_sha="0" * 64,
                                                       raw_file="baseline"), parent)
        self.baseline_sha = sha(p.read_bytes())
        (self.dir / "baseline" / "baseline_manifest.json").write_text(canon({
            "schema_version": "collector-baseline-v1", "cycle_id": self.cid,
            "span_start_utc": days[0] + "T00:00:00Z",
            "span_end_utc": days[-1] + "T00:00:00Z", "candle_count": len(days),
            "baseline_sha256": self.baseline_sha,
            "source_rehearsal_cycle": REH, "source_fetch_ids": [],
            "created_at_utc": "2026-07-31T12:00:00Z"}))
        self._cand_parent = self.baseline_sha
        return self

    def _cand_record(self, day: str, c: dict, raw_sha: str, raw_file: str,
                     run_id="seed", attempt_id="seed-a1", flags=None) -> dict:
        return {"schema_version": "collector-candle-v1", "source": "bitstamp",
                "symbol": "btcusd", "interval": "1d",
                "open_time_ms": day_ts(day) * 1000,
                "open_time_utc": day + "T00:00:00Z",
                "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c["volume"],
                "raw_sha256": raw_sha, "raw_file": raw_file,
                "run_id": run_id, "attempt_id": attempt_id,
                "ingested_at_utc": day + "T05:00:02Z", "flags": flags or []}

    def seed_accepted(self, day: str, c: dict | None = None) -> dict:
        """Full consistent transaction: raw archive + index + candle + run pair."""
        c = c or C(day)
        run_id = f"seed-{day}-{uuid.uuid4().hex[:8]}"
        att = f"{run_id}-a1"
        payload = body(c)
        rel = f"raw/{day[:4]}/{att}-w{day}_{day}.bin"
        f = self.col / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(payload)
        raw_sha = sha(payload)
        self._run_parent = self._append(self.col / "runs.jsonl", {
            "type": "RUN_START", "run_id": run_id, "mode": "daily",
            "params": {}, "started_at_utc": day + "T05:00:00Z"}, self._run_parent)
        self._idx_parent = self._append(self.col / "raw" / "index.jsonl", {
            "schema_version": "collector-raw-index-v2", "run_id": run_id,
            "attempt_id": att, "archived_at_utc": day + "T05:00:01Z",
            "raw_file": rel, "sha256": raw_sha, "byte_len": len(payload),
            "truncated_at_cap": False, "request_url": "https://www.bitstamp.net/api/v2/ohlc/btcusd/",
            "req_start_day": day, "req_end_day": day, "http_status": 200,
            "content_type": "application/json", "content_encoding": None,
            "kind": "daily", "page_no": 1, "retry_no": 0}, self._idx_parent)
        self._cand_parent = self._append(self.col / "candles" / "candles.jsonl",
                                         self._cand_record(day, c, raw_sha, rel,
                                                           run_id, att),
                                         self._cand_parent)
        self._run_parent = self._append(self.col / "runs.jsonl", {
            "type": "RUN_END", "run_id": run_id, "outcome": "OK",
            "reason_codes": ["OK"], "n_attempts": 1, "n_archives": 1,
            "n_candles_appended": 1, "n_incidents": 0,
            "ended_at_utc": day + "T05:00:03Z"}, self._run_parent)
        (self.col / "candles").mkdir(exist_ok=True)
        return {"run_id": run_id, "raw_file": rel, "raw_sha": raw_sha}

    # ---- readers ---------------------------------------------------------- #
    def jsonl(self, rel: str) -> list[dict]:
        p = self.col / rel
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def runs(self):      return self.jsonl("runs.jsonl")
    def index(self):     return self.jsonl("raw/index.jsonl")
    def candles(self):   return self.jsonl("candles/candles.jsonl")
    def incidents(self): return sorted((self.col / "incidents").glob("*.json"))
    def emergencies(self): return sorted((self.col / "emergency").glob("*.json"))
    def frozen(self):    return (self.col / "FROZEN").exists()
    def heartbeat(self):
        return json.loads((self.col / "heartbeat.json").read_text())

    def snapshot(self, *rels) -> dict:
        out = {}
        for rel in rels:
            p = self.col / rel
            out[rel] = sha(p.read_bytes()) if p.exists() else None
        return out

    # ---- recovery / disposition (human-side artifacts, contract schemas) -- #
    def write_recovery(self, incident_name: str, resolution="FALSE_ALARM_RESUME",
                       dispositions=None, acked_runs=None, freeze_hash=None):
        rec = {"schema_version": "collector-recovery-v1",
               "recovered_at_utc": NOW, "freeze_record_hash": freeze_hash or "",
               "incident_ref": incident_name,
               "cause": "test", "audit_evidence": "audited: no candle missing/revised",
               "validity_impact": "none — false alarm",
               "resolution": resolution,
               "orphan_disposition": dispositions or [],
               "acknowledged_incomplete_runs": acked_runs or [],
               "previous_record_hash": GENESIS}
        h = sha(canon(rec)); rec["record_hash"] = h
        p = self.dir / "health" / "recovery.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(canon(rec) + "\n")
        return rec

    def quarantine(self, rel: str, keep_original=False, corrupt_after=False):
        """Perform the §2.2(R4) human disposition for one artifact."""
        did = f"20260803T060000Z-{uuid.uuid4().hex[:12]}"
        src = self.col / rel
        dst = self.dir / "quarantine" / did / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        s = sha(src.read_bytes())
        if keep_original:
            dst.write_bytes(src.read_bytes())          # invalid: copy, original kept
        else:
            os.replace(src, dst)                        # atomic same-fs rename
        if corrupt_after:
            dst.write_bytes(dst.read_bytes() + b"x")    # invalid: sha mismatch
        rec = {"schema_version": "collector-disposition-v1", "disposition_id": did,
               "performed_at_utc": NOW, "performed_by": "anteneh",
               "incident_ref": (self.incidents()[-1].name if self.incidents() else ""),
               "artifacts": [{"before_path": rel,
                              "after_path": f"quarantine/{did}/{rel}",
                              "sha256_as_found": s,
                              "byte_len": dst.stat().st_size}],
               "rationale": "test disposition"}
        h = sha(canon(rec)); rec["record_hash"] = h
        (self.dir / "quarantine" / did / "disposition.json").write_text(canon(rec))
        return {"disposition_id": did, "record_hash": h}


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def col():
    """Import the module under test. RED: fails with ModuleNotFoundError until
    GREEN implements app/operations/collector.py."""
    import app.operations.collector as m          # noqa — intended RED failure
    return m


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "AppSupport" / "MIE"


@pytest.fixture
def live(root) -> Cycle:
    cyc = Cycle(root, CID).seed_baseline(["2026-07-29", "2026-07-30", "2026-07-31"])
    cyc.seed_accepted("2026-08-01")
    return cyc


def run(col, root, transport, mode="daily", cid=CID, now=NOW, fault=None, extra=()):
    argv = ["--cycle-id", cid, "--mode", mode, "--runtime-root", str(root), *extra]
    return col.main(argv, transport=transport, now_utc=now, _fault=fault)


def daily_ok_transport():
    """Valid daily response: overlap 08-01 (matching seed values) + new 08-02."""
    return T(body(C("2026-08-01"), C("2026-08-02")))


# =========================================================================== #
# R2 §  core pipeline (1–11)                                                   #
# =========================================================================== #
class TestAcceptance:
    def test_valid_finalized_daily_candle_accepted(self, col, root, live):
        rc = run(col, root, daily_ok_transport())
        assert rc == EXIT_OK
        cands = live.candles()
        assert [c["open_time_utc"] for c in cands][-1] == "2026-08-02T00:00:00Z"
        new = cands[-1]
        idx = {e["sha256"] for e in live.index()}
        assert new["raw_sha256"] in idx
        assert sha((live.col / new["raw_file"]).read_bytes()) == new["raw_sha256"]
        hb = live.heartbeat()
        assert hb["outcome"] == "OK" and hb["integrity_status"] == "OK"
        assert hb["frozen"] is False and hb["reason_codes"] == ["OK"]

    def test_unfinished_candle_rejected(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-01"), C("2026-08-03"))))  # 08-03 not finalized at NOW
        assert rc == EXIT_FAILED
        assert all(c["open_time_utc"] != "2026-08-03T00:00:00Z" for c in live.candles())
        assert any("CANDLE_UNFINISHED" in i.read_text() for i in live.incidents())

    @pytest.mark.parametrize("field,val", [("open", "NaN"), ("close", "Infinity"),
                                           ("high", "1e99"), ("low", "-5.0"),
                                           ("open", "1" * 40)])
    def test_malformed_ohlc_values(self, col, root, live, field, val):
        bad = C("2026-08-02"); bad[field] = val
        rc = run(col, root, T(body(C("2026-08-01"), bad)))
        assert rc == EXIT_FAILED
        assert len(live.candles()) == 1  # only the seed

    @pytest.mark.parametrize("o,h,l,c", [("100", "90", "80", "85"),   # high<open
                                         ("100", "110", "95", "120"),  # close>high
                                         ("70", "110", "80", "100")])  # open<low
    def test_geometry_violations(self, col, root, live, o, h, l, c):
        rc = run(col, root, T(body(C("2026-08-01"),
                                   C("2026-08-02", o=o, h=h, l=l, c=c))))
        assert rc == EXIT_FAILED
        assert any("CANDLE_GEOMETRY_VIOLATION" in i.read_text()
                   for i in live.incidents())

    def test_negative_volume(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-01"), C("2026-08-02", v="-1.0"))))
        assert rc == EXIT_FAILED

    def test_missing_timestamp(self, col, root, live):
        bad = C("2026-08-02"); del bad["timestamp"]
        rc = run(col, root, T(body(C("2026-08-01"), bad)))
        assert rc == EXIT_FAILED

    def test_misaligned_timestamp(self, col, root, live):
        bad = C("2026-08-02"); bad["timestamp"] = str(day_ts("2026-08-02") + 3600)
        rc = run(col, root, T(body(C("2026-08-01"), bad)))
        assert rc == EXIT_FAILED
        assert any("CANDLE_MISALIGNED_TIMESTAMP" in i.read_text()
                   for i in live.incidents())

    def test_timestamp_out_of_range(self, col, root, live):
        bad = C("2026-08-02"); bad["timestamp"] = "86400"   # 1970 — pre-sanity floor
        rc = run(col, root, T(body(C("2026-08-01"), bad)))
        assert rc == EXIT_FAILED

    def test_body_not_strictly_ascending(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-02"), C("2026-08-01"))))
        assert rc == EXIT_FAILED
        assert any("RESPONSE_SCHEMA_INVALID" in i.read_text()
                   for i in live.incidents())
        assert len(live.candles()) == 1   # whole body rejected

    def test_body_duplicate_timestamp(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-01"), C("2026-08-02"),
                                   C("2026-08-02"))))
        assert rc == EXIT_FAILED

    def test_body_internal_hole_rejected(self, col, root, live):
        # 08-01 then 08-03: intra-body hole => provider integrity, not inter-run gap
        rc = run(col, root, T(body(C("2026-08-01"), C("2026-08-03"))),
                 now="2026-08-04T05:00:00Z")
        assert rc == EXIT_FAILED
        txt = "".join(i.read_text() for i in live.incidents())
        assert "RESPONSE_SCHEMA_INVALID" in txt and "CANDLE_GAP_DETECTED" not in txt


# =========================================================================== #
# R2/R3 §  latch: gap / revision / inert / recovery (12–16)                    #
# =========================================================================== #
class TestLatch:
    def test_historical_gap_latches(self, col, root, live):
        # last accepted 08-01; body offers 08-03/08-04 => 08-02 missing between runs
        rc = run(col, root, T(body(C("2026-08-03"), C("2026-08-04"))),
                 now="2026-08-05T05:00:00Z")
        assert rc == EXIT_FAILED
        assert live.frozen()
        txt = "".join(i.read_text() for i in live.incidents())
        assert "CANDLE_GAP_DETECTED" in txt and "TERMINAL_EVIDENCE" in txt
        assert len(live.candles()) == 1                       # nothing appended
        hb = live.heartbeat()
        assert hb["frozen"] is True and hb["integrity_status"] == "GAP"

    def test_revision_latches(self, col, root, live):
        snap = live.snapshot("candles/candles.jsonl")
        rc = run(col, root, T(body(C("2026-08-01", c="999.00"), C("2026-08-02"))))
        assert rc == EXIT_FAILED and live.frozen()
        assert any("CANDLE_REVISION_DETECTED" in i.read_text()
                   for i in live.incidents())
        assert live.snapshot("candles/candles.jsonl") == snap  # originals intact

    def test_latched_run_is_inert(self, col, root, live):
        (live.col / "FROZEN").write_text(canon(
            {"frozen_at_utc": NOW, "incident_ref": "x", "reason_code": "CANDLE_GAP_DETECTED"}))
        t = Transport()                                        # any call would raise
        rc = run(col, root, t)
        assert rc == EXIT_FROZEN
        assert t.calls == []                                   # zero HTTP attempts
        assert live.runs()[-1]["outcome"] == "FROZEN"
        assert len(live.candles()) == 1

    def test_false_alarm_resume_unlatches(self, col, root, live):
        # CORRUPTION-class latch via orphan archive, then full valid disposition
        orphan = "raw/2026/orphan-a1-w2026-08-02_2026-08-02.bin"
        (live.col / orphan).parent.mkdir(parents=True, exist_ok=True)
        (live.col / orphan).write_bytes(b"orphan-bytes")
        assert run(col, root, Transport()) == EXIT_CORRUPTION  # latches
        disp = live.quarantine(orphan)
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp])
        rc = run(col, root, daily_ok_transport())
        assert rc == EXIT_OK
        hb = live.heartbeat()
        assert hb["frozen"] is False and hb["integrity_status"] == "OK"
        assert hb["recovery_ref"] is not None

    def test_invalid_recovery_stays_frozen(self, col, root, live):
        run(col, root, T(body(C("2026-08-03"), C("2026-08-04"))),
            now="2026-08-05T05:00:00Z")                        # terminal gap latch
        live.write_recovery("wrong-incident-name.json")
        t = Transport()
        rc = run(col, root, t)
        assert rc == EXIT_FROZEN and t.calls == []
        assert live.frozen()


# =========================================================================== #
# R2 §  raw archive discipline (17–20)                                         #
# =========================================================================== #
class TestRawArchive:
    def test_raw_bytes_archived_before_parsing(self, col, root, live):
        payload = b'{"data": {"ohlc": NOT-JSON'
        rc = run(col, root, T(payload))
        assert rc == EXIT_FAILED
        archives = [p for p in (live.col / "raw").rglob("*.bin")]
        assert any(p.read_bytes() == payload for p in archives)
        assert any(e["sha256"] == sha(payload) for e in live.index())

    def test_raw_sha_recomputed_from_disk(self, col, root, live):
        rc = run(col, root, daily_ok_transport())
        assert rc == EXIT_OK
        for e in live.index():
            assert sha((live.col / e["raw_file"]).read_bytes()) == e["sha256"]
            assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"])

    def test_raw_name_independent_of_content(self, col, root, live):
        run(col, root, T(b"\x00\xffhostile<script>bytes"))
        for p in (live.col / "raw").rglob("*.bin"):
            assert re.fullmatch(
                r"\d{8}T\d{6}Z-(daily|rehearsal)-[0-9a-f]{12}-a\d+"
                r"-w\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.bin", p.name), p.name

    def test_run_and_attempt_id_collision_resistance(self, col, root, live):
        run(col, root, daily_ok_transport())
        live2 = Cycle(root / "x", CID).seed_baseline(["2026-07-31"])
        run(col, root / "x", daily_ok_transport(), now=NOW)    # same second
        ids = {r["run_id"] for r in live.runs() + live2.runs()
               if r.get("type") == "RUN_START"}
        assert len(ids) == len([r for r in live.runs() + live2.runs()
                                if r.get("type") == "RUN_START"])


# =========================================================================== #
# R2 §  ordering, heartbeat, fsync faults (21–25)                              #
# =========================================================================== #
class TestDurability:
    @pytest.mark.parametrize("fault", ["after_run_start", "after_archive_publish",
                                       "after_index_append", "after_candle_append"])
    def test_heartbeat_written_last(self, col, root, live, fault):
        before = live.snapshot("heartbeat.json")
        with pytest.raises(Exception) as exc:
            run(col, root, daily_ok_transport(), fault=fault)
        assert exc.type.__name__ == "InjectedFault"
        assert live.snapshot("heartbeat.json") == before

    def test_heartbeat_exposes_integrity(self, col, root, live):
        run(col, root, T(body(C("2026-08-03"), C("2026-08-04"))),
            now="2026-08-05T05:00:00Z")
        hb = live.heartbeat()
        assert hb["integrity_status"] == "GAP" and hb["frozen"] is True
        assert hb["incident_ref"] and hb["recovery_ref"] is None

    def test_archive_fsync_failure(self, col, root, live):
        snap = live.snapshot("raw/index.jsonl", "candles/candles.jsonl")
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="archive_fsync")
        assert live.snapshot("raw/index.jsonl", "candles/candles.jsonl") == snap

    def test_candle_store_fsync_failure(self, col, root, live):
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="candle_fsync")
        # archive + index retained, candle store not advanced
        assert len(live.candles()) == 1
        assert len(live.index()) >= 2

    def test_atomic_rename_failure(self, col, root, live):
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="archive_rename")
        published = list((live.col / "raw").rglob("*.bin"))
        staged = list((live.col / "raw" / ".staging").glob("*"))
        assert len(published) == 1          # only the seed archive
        assert staged                        # residue allowed, unpublished


# =========================================================================== #
# R2/R3 §  chain verification & anchoring (26–28)                              #
# =========================================================================== #
class TestChains:
    def test_chain_verification_on_startup(self, col, root, live):
        p = live.col / "candles" / "candles.jsonl"
        lines = p.read_text().splitlines()
        edited = json.loads(lines[0]); edited["close"] = "42.00"
        lines[0] = canon(edited)
        p.write_text("\n".join(lines) + "\n")
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION and live.frozen()

    def test_truncated_tail_latches(self, col, root, live):
        p = live.col / "runs.jsonl"
        raw = p.read_bytes()
        p.write_bytes(raw[:-20])                               # half-written tail
        as_found = sha(raw[:-20])
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION
        assert sha(p.read_bytes()) == as_found                 # never repaired
        assert live.frozen()

    def test_forward_chain_anchored_to_baseline(self, col, root, live):
        first = live.candles()[0]
        assert first["previous_record_hash"] == live.baseline_sha
        # tamper baseline => anchor mismatch latches
        bp = live.dir / "baseline" / "baseline_candles.jsonl"
        bp.write_bytes(bp.read_bytes() + b" ")
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION and live.frozen()


# =========================================================================== #
# R2 §  transport outcomes (29–36)                                             #
# =========================================================================== #
class TestTransport:
    def test_network_timeout_is_degraded(self, col, root, live):
        snap = live.snapshot("raw/index.jsonl", "candles/candles.jsonl")
        rc = run(col, root, Transport(TimeoutError(), TimeoutError(), TimeoutError()))
        assert rc == EXIT_DEGRADED
        assert live.snapshot("raw/index.jsonl", "candles/candles.jsonl") == snap
        assert not live.frozen()
        assert live.heartbeat()["outcome"] == "DEGRADED"

    def test_5xx_retried_then_degraded_with_attempt_archives(self, col, root, live):
        t = Transport(Resp(status=502, payload=b"bad gateway"),
                      Resp(status=502, payload=b"bad gateway"),
                      Resp(status=502, payload=b"bad gateway"))
        rc = run(col, root, t)
        assert rc == EXIT_DEGRADED
        assert len(t.calls) == 3                                # retry policy
        assert len([e for e in live.index() if e["http_status"] == 502]) == 3
        seqs = sorted(int(e["attempt_id"].rsplit("-a", 1)[1])
                      for e in live.index() if e["http_status"] == 502)
        assert seqs == [1, 2, 3]

    def test_4xx_failed_not_retried(self, col, root, live):
        t = Transport(Resp(status=404, payload=b"nope"))
        rc = run(col, root, t)
        assert rc == EXIT_FAILED and len(t.calls) == 1

    def test_unexpected_content_type(self, col, root, live):
        rc = run(col, root, Transport(Resp(content_type="text/html",
                                           payload=b"<html>")))
        assert rc == EXIT_FAILED
        assert len(live.candles()) == 1                         # never parsed

    def test_unexpected_content_encoding(self, col, root, live):
        rc = run(col, root, Transport(Resp(content_encoding="gzip",
                                           payload=b"\x1f\x8bwirebytes")))
        assert rc == EXIT_FAILED
        assert any(p.read_bytes() == b"\x1f\x8bwirebytes"
                   for p in (live.col / "raw").rglob("*.bin"))  # wire bytes archived

    def test_streaming_cap_enforced(self, col, root, live):
        rc = run(col, root, Transport(Resp(payload=b"x" * (10 * 1024 * 1024 + 1))))
        assert rc == EXIT_FAILED
        e = [e for e in live.index() if e.get("truncated_at_cap")]
        assert e and e[0]["byte_len"] <= 10 * 1024 * 1024

    def test_malformed_json(self, col, root, live):
        rc = run(col, root, T(b"{not json"))
        assert rc == EXIT_FAILED

    def test_empty_daily_fails(self, col, root, live):
        rc = run(col, root, T(json.dumps({"data": {"pair": "BTC/USD",
                                                   "ohlc": []}}).encode()))
        assert rc == EXIT_FAILED
        assert any("RESPONSE_EMPTY_DAILY" in i.read_text()
                   for i in live.incidents())

    def test_rehearsal_termination_ok(self, col, root):
        reh = Cycle(root, REH)
        page1 = body(*[C(f"2026-07-{d:02d}") for d in range(1, 11)])
        page2 = json.dumps({"data": {"pair": "BTC/USD", "ohlc": []}}).encode()
        rc = run(col, root, T(page1, page2), mode="rehearsal", cid=REH,
                 extra=("--start", "2026-07-01", "--end", "2026-07-10"))
        assert rc == EXIT_OK
        assert len(reh.candles()) == 10


# =========================================================================== #
# R2 §  idempotency, lock, containment, CLI, hygiene, baseline (37–42)         #
# =========================================================================== #
class TestStructural:
    def test_identical_rerun_idempotent(self, col, root, live):
        assert run(col, root, daily_ok_transport()) == EXIT_OK
        n = len(live.candles())
        assert run(col, root, daily_ok_transport(), now="2026-08-03T06:00:00Z") == EXIT_OK
        assert len(live.candles()) == n                        # semantic no-op
        assert not live.incidents()

    def test_concurrent_execution_blocked(self, col, root, live):
        import fcntl
        lock = live.col / "collector.lock"
        lock.touch()
        fd = os.open(lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            snap = live.snapshot("runs.jsonl", "heartbeat.json",
                                 "raw/index.jsonl", "candles/candles.jsonl")
            t = Transport()
            rc = run(col, root, t)
            assert rc == EXIT_LOCK_HELD and t.calls == []
            assert live.snapshot("runs.jsonl", "heartbeat.json",
                                 "raw/index.jsonl", "candles/candles.jsonl") == snap
        finally:
            os.close(fd)

    def test_no_writes_outside_runtime_root(self, col, root, live, tmp_path,
                                            monkeypatch):
        outside = tmp_path / "outside"; outside.mkdir()
        monkeypatch.chdir(outside)
        run(col, root, daily_ok_transport())
        run(col, root, T(b"{bad"), now="2026-08-03T06:00:00Z")
        assert list(outside.iterdir()) == []
        repo_markers = [p for p in Path.cwd().rglob("*")
                        if "MIE" not in str(p)]
        assert repo_markers == []

    def test_cli_contract_exit_codes(self, col, root, live):
        assert col.main(["--mode", "daily"], transport=Transport()) == EXIT_USAGE
        assert col.main(["--cycle-id", CID, "--mode", "bogus",
                         "--runtime-root", str(root)],
                        transport=Transport()) == EXIT_USAGE
        assert col.main(["--cycle-id", "BAD ID!", "--mode", "daily",
                         "--runtime-root", str(root)],
                        transport=Transport()) == EXIT_USAGE
        # daily mode rejects --start/--end
        assert col.main(["--cycle-id", CID, "--mode", "daily",
                         "--runtime-root", str(root), "--start", "2026-01-01"],
                        transport=Transport()) == EXIT_USAGE
        # USAGE writes nothing at all
        assert not (root / "cycles" / CID / "collector" / "runs.jsonl").exists() \
            or len(Cycle(root, CID).runs()) == 4               # only the seeds

    def test_no_credentials_no_trading_no_perf_fields(self, col, root, live,
                                                      monkeypatch):
        monkeypatch.setenv("BITSTAMP_API_KEY", "canary-secret-123")
        t = daily_ok_transport()
        assert run(col, root, t) == EXIT_OK
        for call in t.calls:
            assert "canary-secret-123" not in canon(call)
            assert not any(k.lower() in ("authorization", "x-auth", "cookie")
                           for k in call["headers"])
            assert "/ohlc/btcusd/" in call["url"]
        corpus = "".join(p.read_text(errors="replace")
                         for p in live.col.rglob("*") if p.is_file()).lower()
        for tok in PERF_TOKENS:
            assert tok not in corpus, tok

    def test_baseline_boundary_exclusive(self, col, root, live):
        base_days = {c["open_time_utc"][:10]
                     for c in [json.loads(x) for x in
                               (live.dir / "baseline" / "baseline_candles.jsonl")
                               .read_text().splitlines()]}
        assert "2026-08-01" not in base_days                   # test_start excluded
        fwd_days = {c["open_time_utc"][:10] for c in live.candles()}
        assert fwd_days.isdisjoint(base_days)                  # no mixing
        run(col, root, daily_ok_transport())
        assert min({c["open_time_utc"][:10] for c in live.candles()}) >= "2026-08-01"


# =========================================================================== #
# R3 §  crash boundaries of the NORMAL transaction (43–48)                     #
# =========================================================================== #
class TestCrashBoundaries:
    def _crash_then_restart(self, col, root, live, fault):
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault=fault)
        return run(col, root, Transport(), now="2026-08-03T06:00:00Z")

    def test_crash_after_run_start(self, col, root, live):
        rc = self._crash_then_restart(col, root, live, "after_run_start")
        assert rc == EXIT_CORRUPTION and live.frozen()
        txt = "".join(i.read_text() for i in live.incidents())
        assert "RECON_RUN_INCOMPLETE" in txt

    def test_crash_after_archive_before_index(self, col, root, live):
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="after_archive_publish")
        orphans = [p for p in (live.col / "raw").rglob("*.bin")]
        snap = {p: sha(p.read_bytes()) for p in orphans}
        rc = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc == EXIT_CORRUPTION
        txt = "".join(i.read_text() for i in live.incidents())
        assert "RECON_ORPHAN_ARCHIVE" in txt
        assert {p: sha(p.read_bytes()) for p in orphans} == snap  # never adopted

    def test_crash_after_index_before_candle(self, col, root, live):
        rc = self._crash_then_restart(col, root, live, "after_index_append")
        assert rc == EXIT_CORRUPTION
        assert "RECON_RUN_INCOMPLETE" in "".join(i.read_text()
                                                for i in live.incidents())
        assert len(live.candles()) == 1                        # no partial candle

    def test_crash_after_candle_before_run_end(self, col, root, live):
        rc = self._crash_then_restart(col, root, live, "after_candle_append")
        assert rc == EXIT_CORRUPTION
        # candle chain itself is valid; violation is the incomplete run
        txt = "".join(i.read_text() for i in live.incidents())
        assert "RECON_RUN_INCOMPLETE" in txt and "RECON_COUNT_MISMATCH" not in txt

    def test_crash_after_run_end_before_heartbeat(self, col, root, live):
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="after_run_end")
        # lawful lag: reconciliation clean, next run proceeds and heals heartbeat
        t = T(body(C("2026-08-02"), C("2026-08-03")))
        rc = run(col, root, t, now="2026-08-04T05:00:00Z")
        assert rc == EXIT_OK
        assert not live.frozen()
        assert live.heartbeat()["latest_finalized_candle_utc"] == "2026-08-03T00:00:00Z"

    def test_staging_residue_not_corruption(self, col, root, live):
        (live.col / "raw" / ".staging" / "leftover.tmp").write_bytes(b"junk")
        rc = run(col, root, daily_ok_transport())
        assert rc == EXIT_OK and not live.frozen()
        assert (live.col / "raw" / ".staging" / "leftover.tmp").read_bytes() == b"junk"


# =========================================================================== #
# R3 §  orphan / contradiction reconciliation (49–56)                          #
# =========================================================================== #
class TestReconciliation:
    def _expect_latch(self, col, root, live, code):
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION and live.frozen()
        assert any(code in i.read_text() for i in live.incidents()), code

    def test_orphan_index_entry(self, col, root, live):
        target = next((live.col / "raw").rglob("*.bin"))
        os.remove(target)
        self._expect_latch(col, root, live, "RECON_ORPHAN_INDEX")

    def test_candle_missing_raw(self, col, root, live):
        # index+file for a candle removed together => candle→raw dangling
        e = live.index()[0]
        os.remove(live.col / e["raw_file"])
        p = live.col / "raw" / "index.jsonl"; p.write_text("")   # empty chain (valid genesis)
        self._expect_latch(col, root, live, "RECON_CANDLE_RAW_MISSING")

    def test_candle_raw_hash_mismatch(self, col, root, live):
        target = next((live.col / "raw").rglob("*.bin"))
        target.write_bytes(target.read_bytes() + b"tamper")
        self._expect_latch(col, root, live, "RECON_HASH_MISMATCH")

    def test_incident_without_run(self, col, root, live):
        (live.col / "incidents" / "incident_x.json").write_text(canon(
            {"run_id": "ghost-run", "class": "CORRUPTION",
             "reason_code": "CANDLE_GAP_DETECTED", "record_hash": "0" * 64}))
        self._expect_latch(col, root, live, "RECON_INCIDENT_ORPHAN")

    def test_run_end_count_contradiction(self, col, root, live):
        p = live.col / "runs.jsonl"
        lines = p.read_text().splitlines()
        end = json.loads(lines[-1]); assert end["type"] == "RUN_END"
        end["n_candles_appended"] = 2
        # keep chain formally valid: recompute hash so ONLY the count contradicts
        body_ = {k: v for k, v in end.items() if k != "record_hash"}
        end["record_hash"] = sha(canon(body_))
        lines[-1] = canon(end)
        p.write_text("\n".join(lines) + "\n")
        self._expect_latch(col, root, live, "RECON_COUNT_MISMATCH")

    def test_duplicate_run_id(self, col, root, live):
        runs = live.runs()
        start = [r for r in runs if r["type"] == "RUN_START"][0]
        dup = {k: v for k, v in start.items()
               if k not in ("record_hash", "previous_record_hash")}
        live._run_parent = live._append(live.col / "runs.jsonl", dup,
                                        runs[-1]["record_hash"])
        self._expect_latch(col, root, live, "RECON_DUPLICATE_RUN_ID")

    def test_attempt_seq_gap(self, col, root, live):
        e = dict(live.index()[0])
        for k in ("record_hash", "previous_record_hash"):
            e.pop(k)
        e["attempt_id"] = e["run_id"] + "-a3"                  # seq 1 then 3
        e["raw_file"] = "raw/2026/" + e["attempt_id"] + "-w2026-08-01_2026-08-01.bin"
        (live.col / e["raw_file"]).write_bytes(b"page")
        e["sha256"] = sha(b"page"); e["byte_len"] = 4
        live._idx_parent = live._append(live.col / "raw" / "index.jsonl", e,
                                        live.index()[-1]["record_hash"])
        self._expect_latch(col, root, live, "RECON_ATTEMPT_ORPHAN")

    def test_runs_chain_corrupt_uses_emergency(self, col, root, live):
        p = live.col / "runs.jsonl"
        lines = p.read_text().splitlines()
        edited = json.loads(lines[0]); edited["mode"] = "tampered"
        lines[0] = canon(edited)                                # hash now wrong
        p.write_text("\n".join(lines) + "\n")
        snap_runs = sha(p.read_bytes())
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION
        assert live.emergencies()                               # emergency file
        assert sha(p.read_bytes()) == snap_runs                 # NO append behind corruption
        assert not live.incidents()                             # no run-referencing incident
        rc2 = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc2 == EXIT_FROZEN
        assert len(live.emergencies()) == 1                     # deduped


# =========================================================================== #
# R3 §  overlap + terminal semantics (57–58)                                   #
# =========================================================================== #
class TestOverlapAndTerminal:
    def test_missing_overlap_freezes(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-02"))))           # no 08-01 overlap
        assert rc == EXIT_FAILED and live.frozen()
        txt = "".join(i.read_text() for i in live.incidents())
        assert "OVERLAP_MISSING" in txt and "TERMINAL_EVIDENCE" in txt

    def test_overlap_value_revision_freezes(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-01", v="10.01"), C("2026-08-02"))))
        assert rc == EXIT_FAILED and live.frozen()
        assert any("CANDLE_REVISION_DETECTED" in i.read_text()
                   for i in live.incidents())

    def test_terminal_incident_rejects_false_alarm_resume(self, col, root, live):
        run(col, root, T(body(C("2026-08-03"), C("2026-08-04"))),
            now="2026-08-05T05:00:00Z")                         # terminal gap
        live.write_recovery(live.incidents()[-1].name,
                            resolution="FALSE_ALARM_RESUME")
        rc = run(col, root, Transport())
        assert rc == EXIT_FROZEN and live.frozen()              # rejected

    def test_false_alarm_resume_heartbeat(self, col, root, live):
        orphan = "raw/2026/orphan2-a1-w2026-08-02_2026-08-02.bin"
        (live.col / orphan).write_bytes(b"o2")
        run(col, root, Transport())                             # CORRUPTION latch
        disp = live.quarantine(orphan)
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp])
        assert run(col, root, daily_ok_transport()) == EXIT_OK
        hb = live.heartbeat()
        assert (hb["frozen"], hb["integrity_status"]) == (False, "OK")
        assert hb["recovery_ref"] and hb["incident_ref"]        # history retained

    def test_new_cycle_terminal_heartbeat(self, col, root, live):
        run(col, root, T(body(C("2026-08-03"), C("2026-08-04"))),
            now="2026-08-05T05:00:00Z")                         # terminal gap
        live.write_recovery(live.incidents()[-1].name,
                            resolution="NEW_RESEARCH_CYCLE")
        rc = run(col, root, Transport(), now="2026-08-06T05:00:00Z")
        assert rc == EXIT_FROZEN                                # old cycle frozen forever
        assert live.heartbeat()["frozen"] is True


# =========================================================================== #
# R4 §  reconciliation-failure transaction + clarifications (59–68)            #
# =========================================================================== #
class TestReconFailureTransaction:
    @staticmethod
    def _orphan(live, name="rA"):
        rel = f"raw/2026/{name}-a1-w2026-08-02_2026-08-02.bin"
        (live.col / rel).parent.mkdir(parents=True, exist_ok=True)
        (live.col / rel).write_bytes(b"orphan-" + name.encode())
        return rel

    def test_recon_failure_transaction_order(self, col, root, live):
        self._orphan(live)
        n_runs = len(live.runs())
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION
        runs = live.runs()
        start, end = runs[n_runs], runs[n_runs + 1]
        assert start["type"] == "RUN_START" and start["mode"] == "reconciliation"
        assert end["type"] == "RUN_END" and end["outcome"] == "FAILED"
        assert end["n_attempts"] == 0 and end["n_incidents"] == 1
        assert live.frozen()
        inc = json.loads(live.incidents()[-1].read_text())
        assert inc["run_id"] == start["run_id"]                 # A2 bound to A1
        assert "violation_signature" in inc

    def test_recon_failure_regime_b_no_run_start(self, col, root, live):
        # corrupt the CANDLE chain while runs.jsonl stays intact
        p = live.col / "candles" / "candles.jsonl"
        lines = p.read_text().splitlines()
        e = json.loads(lines[0]); e["volume"] = "tampered"
        lines[0] = canon(e); p.write_text("\n".join(lines) + "\n")
        runs_snap = sha((live.col / "runs.jsonl").read_bytes())
        rc = run(col, root, Transport())
        assert rc == EXIT_CORRUPTION
        assert sha((live.col / "runs.jsonl").read_bytes()) == runs_snap  # untouched
        assert live.emergencies() and live.frozen()

    # --- clarification 1: crashes in A1–A3 produce BOTH distinct violations --
    def test_crash_A1_A2_distinct_signatures(self, col, root, live):
        self._orphan(live)
        with pytest.raises(Exception):
            run(col, root, Transport(), fault="reconA_after_run_start")
        rc = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc == EXIT_CORRUPTION
        incs = [json.loads(i.read_text()) for i in live.incidents()]
        codes = {i["reason_code"] for i in incs}
        assert "RECON_ORPHAN_ARCHIVE" in codes                  # original violation
        assert "RECON_RUN_INCOMPLETE" in codes                  # interrupted A-run
        sigs = [i["violation_signature"] for i in incs]
        assert len(sigs) == len(set(sigs))                      # distinct, not merged

    def test_crash_A2_A3(self, col, root, live):
        self._orphan(live)
        with pytest.raises(Exception):
            run(col, root, Transport(), fault="reconA_after_incident")
        n_inc = len(live.incidents())
        rc = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc == EXIT_CORRUPTION
        incs = [json.loads(i.read_text()) for i in live.incidents()]
        orig = [i for i in incs if i["reason_code"] == "RECON_ORPHAN_ARCHIVE"]
        assert len(orig) == 1                                   # signature-deduped
        assert any(i["reason_code"] == "RECON_RUN_INCOMPLETE" for i in incs)
        assert len(live.incidents()) == n_inc + 1               # only the new distinct one

    def test_crash_A3_A4(self, col, root, live):
        self._orphan(live)
        with pytest.raises(Exception):
            run(col, root, Transport(), fault="reconA_after_run_end")
        assert not live.frozen()
        rc = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc in (EXIT_CORRUPTION, EXIT_FROZEN)
        assert live.frozen()                                    # marker completed
        orig = [i for i in live.incidents()
                if json.loads(i.read_text())["reason_code"] == "RECON_ORPHAN_ARCHIVE"]
        assert len(orig) == 1                                   # not duplicated

    def test_crash_A4_A5(self, col, root, live):
        self._orphan(live)
        with pytest.raises(Exception):
            run(col, root, Transport(), fault="reconA_after_frozen")
        assert live.frozen()
        rc = run(col, root, Transport(), now="2026-08-03T06:00:00Z")
        assert rc == EXIT_FROZEN                                # steady state
        hb = live.heartbeat()
        assert hb["frozen"] is True and hb["integrity_status"] == "CORRUPTION"

    # --- disposition protocol (65–67) ---------------------------------------
    def test_valid_disposition_resumes(self, col, root, live):
        rel = self._orphan(live, "d1")
        run(col, root, Transport())                             # latch
        disp = live.quarantine(rel)
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp])
        assert run(col, root, daily_ok_transport()) == EXIT_OK
        assert not (live.col / rel).exists()                    # before_path gone
        q = live.dir / "quarantine" / disp["disposition_id"] / rel
        assert q.exists()                                       # preserved bytes

    def test_invalid_disposition_missing_after_file(self, col, root, live):
        rel = self._orphan(live, "d2")
        run(col, root, Transport())
        disp = live.quarantine(rel)
        q = live.dir / "quarantine" / disp["disposition_id"] / rel
        os.remove(q)                                            # after_path missing
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp])
        assert run(col, root, Transport(), now="2026-08-03T07:00:00Z") == EXIT_FROZEN

    def test_invalid_disposition_sha_or_residue(self, col, root, live):
        rel = self._orphan(live, "d3")
        run(col, root, Transport())
        disp = live.quarantine(rel, corrupt_after=True)         # sha mismatch
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp])
        assert run(col, root, Transport(), now="2026-08-03T07:00:00Z") == EXIT_FROZEN
        rel2 = self._orphan(live, "d4")
        disp2 = live.quarantine(rel2, keep_original=True)       # before_path remains
        live.write_recovery(live.incidents()[-1].name, dispositions=[disp2])
        assert run(col, root, Transport(), now="2026-08-03T08:00:00Z") == EXIT_FROZEN

    # --- clarifications 2 + 3 (68) -------------------------------------------
    def test_acknowledged_incomplete_runs_scope(self, col, root, live):
        # (a) crashed normal run => RECON_RUN_INCOMPLETE; unacknowledged latches
        with pytest.raises(Exception):
            run(col, root, daily_ok_transport(), fault="after_run_start")
        assert run(col, root, Transport(),
                   now="2026-08-03T06:00:00Z") == EXIT_CORRUPTION
        crashed = [r["run_id"] for r in live.runs() if r["type"] == "RUN_START"
                   if r["run_id"] not in
                   {e["run_id"] for e in live.runs() if e["type"] == "RUN_END"}]
        # (b) acknowledged incomplete RUN record => clean resume
        live.write_recovery(live.incidents()[-1].name, acked_runs=crashed)
        assert run(col, root, daily_ok_transport(),
                   now="2026-08-03T07:00:00Z") == EXIT_OK
        # (c) any OTHER embedded chain contradiction is NOT excusable the same way
        p = live.col / "runs.jsonl"
        lines = p.read_text().splitlines()
        end = json.loads([l for l in lines
                          if json.loads(l).get("type") == "RUN_END"][0])
        idx = lines.index(canon(end)) if canon(end) in lines else None
        end["n_archives"] = 99
        b = {k: v for k, v in end.items() if k != "record_hash"}
        end["record_hash"] = sha(canon(b))
        lines = [canon(end) if i == idx else l for i, l in enumerate(lines)]
        p.write_text("\n".join(lines) + "\n")
        assert run(col, root, Transport(),
                   now="2026-08-03T08:00:00Z") == EXIT_CORRUPTION
        live.write_recovery("whatever.json",
                            acked_runs=[end["run_id"]])         # cannot excuse
        assert run(col, root, Transport(),
                   now="2026-08-03T09:00:00Z") == EXIT_FROZEN   # NEW_RESEARCH_CYCLE only


# =========================================================================== #
# R4 §  semantic overlap comparison (69–70)                                    #
# =========================================================================== #
class TestSemanticOverlap:
    def test_overlap_serialization_difference_not_revision(self, col, root, live):
        seed = C("2026-08-01")
        reser = {"volume": "10.0", "close": "105.0000", "low": "90.0",
                 "high": "110.0", "open": "100.0",
                 "timestamp": str(day_ts("2026-08-01"))}       # same VALUES, new forms
        assert Decimal(reser["close"]) == Decimal(seed["close"])
        payload = json.dumps({"data": {"pair": "BTC/USD",
                                       "ohlc": [reser, C("2026-08-02")]}},
                             indent=2).encode()                 # whitespace differs too
        rc = run(col, root, T(payload))
        assert rc == EXIT_OK
        assert not live.incidents() and not live.frozen()
        # raw bytes still archived under their own (different) hash
        assert any(sha(payload) == e["sha256"] for e in live.index())
        # stored serialization of the accepted original is untouched
        assert live.candles()[0]["volume"] == "10.00"

    def test_overlap_canonical_value_revision(self, col, root, live):
        rc = run(col, root, T(body(C("2026-08-01", v="10.000001"),
                                   C("2026-08-02"))))
        assert rc == EXIT_FAILED and live.frozen()
        txt = "".join(i.read_text() for i in live.incidents())
        assert "CANDLE_REVISION_DETECTED" in txt and "TERMINAL_EVIDENCE" in txt
