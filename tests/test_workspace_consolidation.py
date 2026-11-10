"""Runtime identity and honest control-qualification display; no research runs."""
import io
import json
from unittest.mock import patch
import pytest
from app import workspace as W, workspace_launcher as L

def test_runtime_identity_binds_main_based_engine_and_root():
    s = W.status()
    assert s["active_engine"] == "srp-matrix-v4"
    assert s["workspace_identity"]["root"] == str(W.ROOT.resolve())
    assert s["workspace_identity"]["engine"] == s["active_engine"]
    assert len(s["workspace_identity"]["source_sha256"]) == 64

def test_matching_server_is_reused_but_old_or_other_checkout_is_rejected():
    expected = {"root": "/correct", "engine": "srp-matrix-v4", "source_sha256": "a" * 64}
    good = {"app_id": "mie-local-workspace", "workspace_identity": expected}
    assert L.compatible_server(good, expected)
    assert not L.compatible_server({"app_id": "mie-local-workspace"}, expected)
    for field in expected:
        changed = dict(expected, **{field: "different"})
        assert not L.compatible_server(dict(good, workspace_identity=changed), expected)

def test_launcher_refuses_wrong_existing_workspace_without_replacing_it():
    old = io.BytesIO(json.dumps({"app_id": "mie-local-workspace"}).encode())
    with patch.object(L.urllib.request, "urlopen", return_value=old), \
         patch.object(L.uvicorn, "run") as run, patch.object(L.webbrowser, "open") as browser, \
         patch("sys.argv", ["mie-workspace", "--no-browser"]):
        with pytest.raises(SystemExit, match="annen eller eldre"):
            L.main()
        run.assert_not_called()
        browser.assert_not_called()

def test_failed_qualification_is_visible_without_changing_method_or_defaults():
    import inspect
    from app.research_safety import promotion_gate, target_control
    gate = W.status()["control_gate"]
    assert gate["qualification"] == "NOT_QUALIFIED"
    assert gate["candidate_status"] == "NOT_ACCEPTED_REPORTED"
    assert gate["raw_final_results_located"] is False
    assert "ikke kvalifisert" in W.status()["version_notice"]
    assert inspect.signature(promotion_gate.evaluate_promotion).parameters["control_test"].default == "legacy"
    assert inspect.signature(target_control.compare).parameters["sharpe_tol"].default == 0.05
