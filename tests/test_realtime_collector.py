import builtins
import json
import plistlib
from unittest.mock import patch

import pytest

from app.data.realtime_collector import collect_one, init_root, inspect_root, line, main


class Socket:
    def __init__(self, items):
        self.items = iter(items)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def send(self, message): assert json.loads(message)["product_ids"] == ["BTC-USD"]
    def recv(self, timeout):
        try:
            value = next(self.items)
        except StopIteration:
            raise TimeoutError from None
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.fixture
def root(tmp_path):
    path = tmp_path.resolve() / "raw-intake"
    init_root(path, "test fixture")
    return path


def records(path):
    return [json.loads(v) for v in path.read_bytes().splitlines()]


def capture(root, *items, **kwargs):
    return collect_one(root, connect=lambda *a, **k: Socket(items), **kwargs)


def test_restart_preserves_sealed_and_links_next(root):
    first = capture(root, '{"type":"ticker"}', max_messages=1)
    old = (root / first["file"]).read_bytes()
    second = capture(root, b"malformed", max_messages=1)
    assert (root / first["file"]).read_bytes() == old
    header = records(root / second["file"])[0]
    assert header["previous"]["sha256"] == first["sha256"]
    assert header["previous"]["last_received_utc"] == first["last_received_utc"]
    assert header["continuity"] == "UNKNOWN_AT_CONNECTION_BOUNDARY"
    assert not second["complete_trade_history"]
    assert inspect_root(root)["next_number"] == 3


def test_trade_id_diagnostics_across_restart_keep_raw_messages(root):
    def ticker(trade_id):
        return json.dumps(dict(type="ticker", product_id="BTC-USD", price="100",
                               best_bid="99", best_ask="101", best_bid_size="2",
                               best_ask_size="2", last_size="1", trade_id=trade_id,
                               time="2026-11-19T10:00:00Z"))
    first = capture(root, ticker(10), max_messages=1)
    second = capture(root, ticker(10), ticker(15), max_messages=2)
    assert first["last_trade_id"] == 10
    assert second["duplicates_or_reordered"] == 1
    assert second["trade_id_jumps"] == 1
    assert second["messages"] == 2 and second["last_trade_id"] == 15
    assert inspect_root(root)["last"]["sha256"] == second["sha256"]


def test_interruption_preserves_partial_then_resumes(root):
    with pytest.raises(KeyboardInterrupt):
        capture(root, "first", KeyboardInterrupt())
    partial = root / "000000000001.partial"
    before = partial.read_bytes()
    result = capture(root, "second", max_messages=1)
    assert partial.read_bytes() == before
    assert result["file"] == "000000000002.jsonl"
    assert records(root / result["file"])[0]["unfinished_count"] == 1
    assert inspect_root(root)["partials"] == [partial.name]


def test_network_failure_becomes_gap(root):
    result = capture(root, "first", OSError("offline"))
    assert result["reason"] == "connection_failed:OSError"
    assert any(r["kind"] == "gap" for r in records(root / result["file"]))
    assert capture(root, "new", max_messages=1)["file"] == "000000000002.jsonl"


def test_wall_clock_pause_marked_and_next_capture_resumes(root):
    times = iter(["2026-11-19T10:00:00+00:00", "2026-11-19T10:00:00+00:00",
                  "2026-11-19T10:02:00+00:00", "2026-11-19T10:02:01+00:00",
                  "2026-11-19T10:02:01+00:00"])
    result = capture(root, "after sleep", clock=lambda: next(times))
    assert result["reason"] == "wall_clock_pause_or_change"
    assert result["messages"] == 1
    assert any(r.get("reason") == "wall_clock_pause_or_change" for r in records(root / result["file"]))
    assert capture(root, "resumed", max_messages=1)["file"] == "000000000002.jsonl"


@pytest.mark.parametrize("timeout_after_deadline", [True, False])
def test_sleep_past_deadline_without_next_message_records_gap(root, timeout_after_deadline):
    # Simulate the machine sleeping for five minutes while either recv or
    # the next loop condition is pending. Neither path receives a frame.
    stamps = iter(["2026-11-19T10:00:00+00:00", "2026-11-19T10:00:00+00:00",
                   "2026-11-19T10:05:00+00:00"])
    ticks = iter([0., 0., 0., 300.] if timeout_after_deadline else [0., 300.])
    result = capture(root, TimeoutError(), seconds=60, clock=lambda: next(stamps),
                     monotonic=lambda: next(ticks))
    assert result["reason"] == "wall_clock_pause_or_change"
    gap = next(r for r in records(root / result["file"]) if r["kind"] == "gap")
    assert gap["previous_utc"] == "2026-11-19T10:00:00+00:00"
    assert gap["received_utc"] == "2026-11-19T10:05:00+00:00"
    assert result["messages"] == 0
    assert inspect_root(root)["last"]["sha256"] == result["sha256"]


def test_shutdown_between_segments_has_explicit_gap(root):
    first_time = "2026-11-19T10:00:00+00:00"
    second_time = "2026-11-20T08:00:00+00:00"
    first = capture(root, "first", max_messages=1, clock=lambda: first_time)
    second = capture(root, "second", max_messages=1, clock=lambda: second_time)
    rows = records(root / second["file"])
    assert rows[0]["previous"]["sha256"] == first["sha256"]
    gap = next(r for r in rows if r.get("reason") == "between_connections_pause_or_change")
    assert (gap["previous_utc"], gap["received_utc"]) == (first_time, second_time)
    assert inspect_root(root)["next_number"] == 3


def test_footer_tamper_fails_closed(root):
    result = capture(root, "first", max_messages=1)
    path = root / result["file"]
    rows = records(path); rows[-1]["reason"] = "changed"
    path.write_bytes(b"".join(line(row) for row in rows))
    with pytest.raises(ValueError, match="invalid sealed segment"):
        inspect_root(root)
    with pytest.raises(ValueError, match="invalid sealed segment"):
        capture(root, "next", max_messages=1)
    assert not (root / "000000000002.partial").exists()


def test_capacity_stops_without_deleting(root):
    with patch("app.data.realtime_collector.shutil.disk_usage", return_value=type("Disk", (), {"free": 1})()):
        with pytest.raises(ValueError, match="capacity limit"):
            capture(root, "first")
    assert inspect_root(root)["sealed"] == []


def test_requires_initialized_root_and_no_overwrite(root, tmp_path):
    with pytest.raises(FileExistsError): init_root(root, "other")
    with pytest.raises(ValueError, match="initialized root"):
        capture(tmp_path.resolve() / "missing", "first")
    assert not (tmp_path / "missing").exists()


def test_injected_connection_no_websocket_dependency(root):
    real_import = builtins.__import__
    def no_websockets(name, *args, **kwargs):
        if name == "websockets" or name.startswith("websockets."):
            raise ModuleNotFoundError(name=name)
        return real_import(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=no_websockets):
        assert capture(root, "first", max_messages=1)["messages"] == 1


def test_launchd_config_is_read_only_and_resumes_at_login(root, capsys):
    before = set(root.iterdir())
    assert main(["--root", str(root), "launchd-plist"]) == 0
    config = plistlib.loads(capsys.readouterr().out.encode())
    assert config["RunAtLoad"] and config["StartInterval"] == 60
    assert config["ProgramArguments"][3:6] == ["--root", str(root), "collect"]
    assert set(root.iterdir()) == before
