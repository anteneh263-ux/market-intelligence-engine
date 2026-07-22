"""Filesystem store for forward tests. Each test lives under
knowledge_base/forward_tests/<forward_test_id>/ with a locked manifest, an append-only
event log, append-only observations, and an evaluation report. Writes are atomic; the
event and observation logs are append-only (history is never overwritten)."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from app.strategy_research.dossier import _KB_ROOT

from .manifest import canonical_json, code_hash, param_hash, sha256_file


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: str, obj: Dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_jsonl(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    out: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class ForwardStore:
    def __init__(self, root: Optional[str] = None) -> None:
        self.base = os.path.join(root or _KB_ROOT, "forward_tests")
        os.makedirs(self.base, exist_ok=True)
        self.index_path = os.path.join(self.base, "index.json")

    def dir(self, ftid: str) -> str:
        return os.path.join(self.base, ftid)

    def manifest_path(self, ftid: str) -> str:
        return os.path.join(self.dir(ftid), "manifest.json")

    def events_path(self, ftid: str) -> str:
        return os.path.join(self.dir(ftid), "events.jsonl")

    def observations_path(self, ftid: str) -> str:
        return os.path.join(self.dir(ftid), "observations.jsonl")

    def evaluation_path(self, ftid: str) -> str:
        return os.path.join(self.dir(ftid), "evaluation.json")

    def exists(self, ftid: str) -> bool:
        return os.path.exists(self.manifest_path(ftid))

    def write_manifest(self, manifest: Dict) -> None:
        ftid = manifest["forward_test_id"]
        os.makedirs(self.dir(ftid), exist_ok=True)
        _atomic_write(self.manifest_path(ftid), canonical_json(manifest))
        self._update_index()

    def load_manifest(self, ftid: str) -> Dict:
        with open(self.manifest_path(ftid), "r", encoding="utf-8") as f:
            return json.load(f)

    def append_event(self, ftid: str, from_status: Optional[str], to_status: str,
                     reason: str, ts: str, detail: Optional[Dict] = None) -> None:
        _append_jsonl(self.events_path(ftid),
                      {"ts": ts, "from": from_status, "to": to_status,
                       "reason": reason, "detail": detail or {}})

    def read_events(self, ftid: str) -> List[Dict]:
        return _read_jsonl(self.events_path(ftid))

    def set_status(self, manifest: Dict, new_status: str, reason: str, ts: str,
                   detail: Optional[Dict] = None) -> Dict:
        old = manifest.get("status")
        manifest["status"] = new_status
        self.write_manifest(manifest)
        self.append_event(manifest["forward_test_id"], old, new_status, reason, ts, detail)
        return manifest

    def append_observation(self, ftid: str, obs: Dict) -> None:
        obs = dict(obs)
        obs["data_partition"] = "forward"   # physically + logically separate from training data
        _append_jsonl(self.observations_path(ftid), obs)

    def read_observations(self, ftid: str) -> List[Dict]:
        return _read_jsonl(self.observations_path(ftid))

    def write_evaluation(self, ftid: str, report: Dict) -> None:
        _atomic_write(self.evaluation_path(ftid), canonical_json(report))

    def list_tests(self) -> List[Dict]:
        out: List[Dict] = []
        if not os.path.isdir(self.base):
            return out
        for name in sorted(os.listdir(self.base)):
            mp = self.manifest_path(name)
            if os.path.exists(mp):
                m = json.load(open(mp))
                out.append({"forward_test_id": m["forward_test_id"], "strategy_id": m["strategy_id"],
                            "status": m["status"], "start_date": m["start_date"],
                            "strategy_version": m["strategy_version"]})
        return out

    def _update_index(self) -> None:
        _atomic_write(self.index_path, canonical_json({"tests": self.list_tests()}))

    def integrity_check(self, manifest: Dict, strategy) -> Dict:
        """Recompute the decisive hashes (class code + params) and report exactly what
        changed. File-level SHA-256 drift is reported as informational only (an unrelated
        edit to the same source file should not invalidate a locked test)."""
        changes: List[Dict] = []
        file_changes: List[Dict] = []
        cur_code = code_hash(strategy)
        if cur_code != manifest.get("code_hash"):
            changes.append({"field": "code_hash", "locked": manifest.get("code_hash"), "current": cur_code})
        cur_param = param_hash(getattr(strategy, "default_params", {}) or {})
        if cur_param != manifest.get("param_hash"):
            changes.append({"field": "param_hash", "locked": manifest.get("param_hash"), "current": cur_param})
        for fe in manifest.get("strategy_files", []):
            cur = sha256_file(fe.get("path", ""))
            if cur != fe.get("sha256"):
                file_changes.append({"path": fe.get("path"), "locked": fe.get("sha256"), "current": cur})
        return {"ok": not changes, "changes": changes, "file_changes": file_changes}
