"""V2.7.1 UI hardening: run isolation, SSE replay, synthesis cannot define truth."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Barrier, Event, Thread

from ai_lab.core.enums import EvidenceKind
from ai_lab.core.models import Claim, MathCheckResult, RunEvent
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
from ai_lab.ui.http import make_server
from ai_lab.ui.report import build_result_view
from ai_lab.ui.service import get_result

REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, name: str) -> ProjectStore:
    root = tmp_path / "projects" / name
    root.mkdir(parents=True)
    (root / "problem.md").write_text(f"# {name}\n", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()
    return store


def _write_manifest(store: ProjectStore, run_id: str, *, engineering: str, final_state: str = "COMPLETED") -> dict:
    run_dir = store.root / ".runs" / run_id
    (run_dir / "reviews").mkdir(parents=True, exist_ok=True)
    (run_dir / "planner").mkdir(parents=True, exist_ok=True)
    (run_dir / "claims").mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "project_id": store.name,
        "final_state": final_state,
        "engineering_outcome": engineering,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "planner" / "task_graph.json").write_text(
        json.dumps({"graph_id": "t", "tasks": []}), encoding="utf-8"
    )
    return manifest


def _seed_accepted_claim(
    store: ProjectStore,
    run_id: str,
    *,
    claim_id: str,
    statement: str,
) -> None:
    """Authoritative accepted quantitative claim + matching check report."""
    claim = Claim(
        claim_id=claim_id,
        run_id=run_id,
        project_id=store.name,
        statement=statement,
        kind=EvidenceKind.CALCULATION,
        computation_artifact_id=f"comp_{claim_id}",
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    rel = f".runs/{run_id}/claims/{claim.claim_id}_v{claim.version}.json"
    store.write_json(rel, claim.model_dump(mode="json"))
    check = {
        "results": [
            MathCheckResult(
                check_id=f"chk_{claim_id}",
                passed=True,
                details={"claim_id": claim_id},
            ).model_dump(mode="json")
        ],
        "verification_results": [],
        "critical_failures": [],
    }
    store.write_json(
        f".runs/{run_id}/reviews/review_bundle.json",
        {"run_id": run_id, "check_report": check, "claims": []},
    )
    store.write_json(
        f".runs/{run_id}/reviews/last_adjudication.json",
        {"status": "PASS", "engineering_outcome": "PASS"},
    )


def test_run_scoped_final_report_not_project_latest(tmp_path: Path) -> None:
    store = _project(tmp_path, "heater")
    _write_manifest(store, "run_a", engineering="PASS")
    (store.root / ".runs" / "run_a" / "final_report.md").write_text("REPORT A", encoding="utf-8")
    _write_manifest(store, "run_b", engineering="PASS")
    (store.root / ".runs" / "run_b" / "final_report.md").write_text("REPORT B", encoding="utf-8")
    # Legacy last-writer-wins file must not leak into a specific run.
    (store.root / "final_report.md").write_text("LEGACY PROJECT REPORT", encoding="utf-8")

    a = build_result_view(
        store, "run_a", manifest=json.loads((store.root / ".runs" / "run_a" / "manifest.json").read_text())
    )
    b = build_result_view(
        store, "run_b", manifest=json.loads((store.root / ".runs" / "run_b" / "manifest.json").read_text())
    )
    assert a["final_report_markdown"] == "REPORT A"
    assert b["final_report_markdown"] == "REPORT B"
    assert "LEGACY" not in (a["final_report_markdown"] or "")
    assert "REPORT B" not in (a["final_report_markdown"] or "")
    assert "REPORT A" not in (b["final_report_markdown"] or "")


def test_http_result_isolation_same_project_two_runs(tmp_path: Path) -> None:
    store = _project(tmp_path, "heater")
    _write_manifest(store, "run_a", engineering="PASS")
    (store.root / ".runs" / "run_a" / "final_report.md").write_text("REPORT A", encoding="utf-8")
    _write_manifest(store, "run_b", engineering="INSUFFICIENT_EVIDENCE")
    (store.root / ".runs" / "run_b" / "final_report.md").write_text("REPORT B", encoding="utf-8")
    (store.root / "final_report.md").write_text("LEGACY", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.yaml").write_text(
        (REPO / "config" / "default.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    httpd = make_server("127.0.0.1", 0, repo_root=tmp_path)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/runs/run_a/result")
        payload_a = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/runs/run_b/result")
        payload_b = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        assert payload_a["final_report_markdown"] == "REPORT A"
        assert payload_b["final_report_markdown"] == "REPORT B"
        assert payload_a["engineering_outcome"] == "PASS"
        assert payload_b["engineering_outcome"] == "INSUFFICIENT_EVIDENCE"
    finally:
        httpd.shutdown()


def test_cross_run_key_numbers_do_not_contaminate(tmp_path: Path) -> None:
    store = _project(tmp_path, "mixed")
    _write_manifest(store, "run_a", engineering="PASS")
    _seed_accepted_claim(store, "run_a", claim_id="claim_power", statement="heater_power = 3.27 kW")
    _write_manifest(store, "run_b", engineering="PASS")
    _seed_accepted_claim(store, "run_b", claim_id="claim_vib", statement="vibration_frequency = 120 Hz")

    a = get_result("run_a", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    b = get_result("run_b", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    dumped_a = json.dumps(a)
    dumped_b = json.dumps(b)
    assert "3.27 kW" in dumped_a
    assert "120 Hz" not in dumped_a
    assert "120 Hz" in dumped_b
    assert "3.27 kW" not in dumped_b
    assert a["engineering_outcome"] == "PASS"
    assert b["engineering_outcome"] == "PASS"


def test_isolation_different_projects(tmp_path: Path) -> None:
    pa = _project(tmp_path, "proj_a")
    pb = _project(tmp_path, "proj_b")
    _write_manifest(pa, "run_a", engineering="PASS")
    (pa.root / ".runs" / "run_a" / "final_report.md").write_text("ALPHA", encoding="utf-8")
    _write_manifest(pb, "run_b", engineering="FAIL")
    (pb.root / ".runs" / "run_b" / "final_report.md").write_text("BETA", encoding="utf-8")
    a = get_result("run_a", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    b = get_result("run_b", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    assert a["final_report_markdown"] == "ALPHA"
    assert b["final_report_markdown"] == "BETA"
    assert a["project_id"] == "proj_a"
    assert b["project_id"] == "proj_b"


def test_completed_run_unaffected_by_later_running_run(tmp_path: Path) -> None:
    store = _project(tmp_path, "seq")
    _write_manifest(store, "run_a", engineering="PASS")
    (store.root / ".runs" / "run_a" / "final_report.md").write_text("DONE A", encoding="utf-8")
    _write_manifest(store, "run_b", engineering="PENDING", final_state="UNDERSTANDING")
    a = get_result("run_a", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    b = get_result("run_b", repo_root=tmp_path, projects_dir=tmp_path / "projects")
    assert a["final_report_markdown"] == "DONE A"
    assert b["final_report_markdown"] is None
    assert a["engineering_outcome"] == "PASS"


def test_synthesis_poison_does_not_override_accepted_claim(tmp_path: Path) -> None:
    store = _project(tmp_path, "poison")
    manifest = _write_manifest(store, "run_p", engineering="PASS")
    _seed_accepted_claim(store, "run_p", claim_id="claim_power", statement="heater_power = 3.1 kW")
    (store.root / ".runs" / "run_p" / "reviews" / "synthesis_bundle.json").write_text(
        json.dumps(
            {
                "report_gate": "PASS",
                "narrative": "The required power is 99.9 kW.",
                "verified_results": [{"claim_id": "claim_power", "statement": "heater_power = 99.9 kW"}],
                "accepted_claims": [{"claim_id": "claim_power", "statement": "heater_power = 99.9 kW"}],
            }
        ),
        encoding="utf-8",
    )
    view = build_result_view(store, "run_p", manifest=manifest)
    dumped = json.dumps(view["key_numbers"])
    assert "3.1 kW" in dumped
    assert "99.9" not in dumped
    assert view["engineering_outcome"] == "PASS"


def test_empty_accepted_claims_no_supported_key_number_from_narrative(tmp_path: Path) -> None:
    store = _project(tmp_path, "empty")
    manifest = _write_manifest(store, "run_e", engineering="INSUFFICIENT_EVIDENCE")
    (store.root / ".runs" / "run_e" / "reviews" / "last_adjudication.json").write_text(
        json.dumps({"status": "INSUFFICIENT_EVIDENCE", "engineering_outcome": "INSUFFICIENT_EVIDENCE"}),
        encoding="utf-8",
    )
    (store.root / ".runs" / "run_e" / "reviews" / "synthesis_bundle.json").write_text(
        json.dumps(
            {
                "report_gate": "PASS",
                "narrative": "The required power is 3.2 kW.",
                "verified_results": [{"statement": "3.2 kW", "claim_id": "invented"}],
            }
        ),
        encoding="utf-8",
    )
    view = build_result_view(store, "run_e", manifest=manifest)
    assert view["engineering_outcome"] == "INSUFFICIENT_EVIDENCE"
    assert view["key_numbers"] == []
    assert "3.2 kW" not in json.dumps(view["key_numbers"])


def test_invented_synthesis_quantity_is_dropped(tmp_path: Path) -> None:
    store = _project(tmp_path, "extra")
    manifest = _write_manifest(store, "run_x", engineering="PASS")
    _seed_accepted_claim(store, "run_x", claim_id="claim_power", statement="power = 3.2 kW")
    # Second accepted claim — energy. Re-write check report covering both after second seed
    claim_e = Claim(
        claim_id="claim_energy",
        run_id="run_x",
        project_id=store.name,
        statement="energy = 5.8 MJ",
        kind=EvidenceKind.CALCULATION,
        computation_artifact_id="comp_energy",
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    store.write_json(
        ".runs/run_x/claims/claim_energy_v1.json",
        claim_e.model_dump(mode="json"),
    )
    check = {
        "results": [
            MathCheckResult(check_id="c1", passed=True, details={"claim_id": "claim_power"}).model_dump(mode="json"),
            MathCheckResult(check_id="c2", passed=True, details={"claim_id": "claim_energy"}).model_dump(mode="json"),
        ],
        "verification_results": [],
        "critical_failures": [],
    }
    store.write_json(
        ".runs/run_x/reviews/review_bundle.json",
        {"run_id": "run_x", "check_report": check, "claims": []},
    )
    (store.root / ".runs" / "run_x" / "reviews" / "synthesis_bundle.json").write_text(
        json.dumps(
            {
                "narrative": "Efficiency is 94%.",
                "verified_results": [
                    {"claim_id": "claim_power", "statement": "power = 3.2 kW"},
                    {"claim_id": "claim_energy", "statement": "energy = 5.8 MJ"},
                    {"claim_id": "invented_eff", "statement": "efficiency = 94%"},
                ],
            }
        ),
        encoding="utf-8",
    )
    view = build_result_view(store, "run_x", manifest=manifest)
    dumped = json.dumps(view["key_numbers"])
    assert "3.2 kW" in dumped
    assert "5.8 MJ" in dumped
    assert "94%" not in dumped
    assert "efficiency" not in dumped.lower()


def test_deterministic_pass_survives_nonsense_synthesis(tmp_path: Path) -> None:
    store = _project(tmp_path, "status")
    manifest = _write_manifest(store, "run_s", engineering="PASS")
    (store.root / ".runs" / "run_s" / "reviews" / "last_adjudication.json").write_text(
        json.dumps({"status": "PASS", "engineering_outcome": "PASS"}),
        encoding="utf-8",
    )
    (store.root / ".runs" / "run_s" / "reviews" / "synthesis_bundle.json").write_text(
        json.dumps({"report_gate": "FAIL", "narrative": "This is nonsense and definitely FAIL."}),
        encoding="utf-8",
    )
    view = build_result_view(store, "run_s", manifest=manifest)
    assert view["engineering_outcome"] == "PASS"


def test_subscribe_snapshot_and_live_are_gapless(tmp_path: Path) -> None:
    sink = RunEventSink(tmp_path / "events.jsonl")
    for i in range(3):
        sink.emit(RunEvent(run_id="r", message=str(i), event_id=f"pre{i}"))

    live: list[str] = []
    barrier = Barrier(2)

    def subscriber() -> None:
        barrier.wait()
        snap = sink.subscribe(lambda ev: live.append(ev.event_id))
        collected["snap"] = [e.event_id for e in snap]

    def emitter() -> None:
        barrier.wait()
        sink.emit(RunEvent(run_id="r", message="4", event_id="live4"))
        sink.emit(RunEvent(run_id="r", message="5", event_id="live5"))

    collected: dict[str, list[str]] = {}
    t_sub = Thread(target=subscriber)
    t_em = Thread(target=emitter)
    t_sub.start()
    t_em.start()
    t_sub.join(timeout=5)
    t_em.join(timeout=5)
    snap_ids = collected["snap"]
    disk = [e.event_id for e in sink.read_all()]
    union = set(snap_ids) | set(live)
    assert union == set(disk)
    assert set(snap_ids).isdisjoint(set(live))
    assert "live4" in union and "live5" in union


def test_subscribe_under_concurrent_emits_loses_nothing(tmp_path: Path) -> None:
    sink = RunEventSink(tmp_path / "race.jsonl")
    stop = Event()
    emitted: list[str] = []

    def hammer() -> None:
        n = 0
        while not stop.is_set() or n < 20:
            eid = f"h{n}"
            sink.emit(RunEvent(run_id="r", message="h", event_id=eid))
            emitted.append(eid)
            n += 1
            if n >= 80:
                break

    live: list[str] = []
    worker = Thread(target=hammer)
    worker.start()
    # Subscribe while the producer is running — no sleep-based race window.
    snap = sink.subscribe(lambda ev: live.append(ev.event_id))
    stop.set()
    worker.join(timeout=5)
    sink.emit(RunEvent(run_id="r", message="tail", event_id="tail"))
    snap_ids = [e.event_id for e in snap]
    disk = [e.event_id for e in sink.read_all()]
    union = set(snap_ids) | set(live)
    assert union == set(disk)
    assert len(snap_ids) + len(live) == len(disk)
    assert "tail" in live
    assert "tail" not in snap_ids


def test_sse_reconnect_replays_events_without_new_run(tmp_path: Path) -> None:
    sink = RunEventSink(tmp_path / "reconnect.jsonl")
    for i in range(1, 6):
        sink.emit(RunEvent(run_id="r", message=str(i), event_id=f"e{i}"))

    first_live: list[str] = []

    def on_first(ev: RunEvent) -> None:
        first_live.append(ev.event_id)

    snap1 = sink.subscribe(on_first)
    assert [e.event_id for e in snap1] == [f"e{i}" for i in range(1, 6)]
    sink.remove_listener(on_first)

    for i in range(6, 9):
        sink.emit(RunEvent(run_id="r", message=str(i), event_id=f"e{i}"))

    snap2 = sink.subscribe(lambda ev: None)
    ids = [e.event_id for e in snap2]
    assert ids == [f"e{i}" for i in range(1, 9)]
    # Reconnect must not require a new run id / new sink path.
    assert sink.path.name == "reconnect.jsonl"
    assert first_live == []


def test_budget_exceeded_summary_shows_token_reason(tmp_path: Path) -> None:
    """FAILED + error=None used to hide max_tokens exceeded behind a generic sentence."""
    store = _project(tmp_path, "budget")
    manifest = _write_manifest(
        store, "run_bgt", engineering="PENDING", final_state="BUDGET_EXCEEDED"
    )
    manifest["budget"] = {
        "max_tokens": 500000,
        "tokens_used": 540236,
        "max_agent_calls": 100,
        "agent_calls": 20,
        "max_tool_calls": 200,
        "tool_calls": 10,
        "max_cost": 50.0,
        "cost_used": 1.0,
        "max_runtime_seconds": 3600,
        "max_iterations": 24,
    }
    (store.root / ".runs" / "run_bgt" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    view = build_result_view(
        store,
        "run_bgt",
        manifest=manifest,
        lifecycle={"status": "FAILED", "error": None},
    )
    assert view["error"] == "max_tokens exceeded: 540236>500000"
    assert "max_tokens exceeded: 540236>500000" in view["executive_summary"]
    assert "before an engineering result was produced" not in view["executive_summary"]
