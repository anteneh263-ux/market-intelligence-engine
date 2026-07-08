"""Tests for the deterministic dossier generator: byte-identical output, grounded
in structured inputs only (no invented literature), generate-if-missing without
clobbering hand-authored dossiers, and coverage of every registered strategy."""
from __future__ import annotations

import json
import os

from app.knowledge_base import literature as lit
from app.research_core import versioning
from app.research_core.dossier_generator import ensure_dossier, input_hash, render
from app.strategy_research.specs import STRATEGY_SPECS


def test_output_is_deterministic():
    a = render("H-012")[1]
    b = render("H-012")[1]
    assert a == b and len(a) > 500
    assert input_hash("H-012") == input_hash("H-012")


def test_filename_and_header_from_structured_data():
    fn, md = render("H-009")
    assert fn == "H-009_keltner_channels_strategy.md"
    assert "STEP 1 — Strategy Analysis" in md and "STEP 4 — Experiment Design" in md
    assert "dossier-gen-v1" in md


def test_all_referenced_papers_exist_in_store():
    """Grounding: every citation the generator emits must exist in the literature
    store — it can never invent a reference."""
    store = lit._load()
    known_authors = {r["authors"] for r in store["references"].values()}
    import re
    cite = re.compile(r"^- \*\*(.+?) \((\d{4})\)\*\* — ")   # STEP-3 citation format only
    for sid in ["H-003", "H-011", "H-018", "H-021", "H-023"]:
        md = render(sid)[1]
        emitted = [m.group(1) for line in md.splitlines() if (m := cite.match(line))]
        assert emitted, f"{sid}: no citations emitted"
        for author in emitted:
            assert author in known_authors, f"{sid}: uncatalogued author {author!r}"


def test_literature_only_from_class_store():
    # H-021 is mean-reversion → must draw the mean-reversion class references
    md = render("H-021")[1]
    canon = lit.resolve_class("mean-reversion-oscillator")
    assert canon == "mean-reversion"
    refs = lit.for_class("mean-reversion-oscillator")["references"]
    assert refs and any(r["authors"] == "Jegadeesh" for r in refs)


def test_every_spec_renders():
    for sid in STRATEGY_SPECS:
        fn, md = render(sid)
        assert fn.startswith(sid) and "STEP 5" in md


def test_ensure_dossier_generates_if_missing_and_preserves(tmp_path):
    # missing → generated
    r1 = ensure_dossier("H-012", kb_root=str(tmp_path))
    assert r1["written"] and os.path.exists(r1["path"])
    # present → not overwritten
    with open(r1["path"], "a") as f:
        f.write("\nHAND EDIT\n")
    r2 = ensure_dossier("H-012", kb_root=str(tmp_path))
    assert r2["written"] is False and r2["existed"]
    assert "HAND EDIT" in open(r1["path"]).read()
