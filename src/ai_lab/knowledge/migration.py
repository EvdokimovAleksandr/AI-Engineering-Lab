"""Migrate legacy flat claims into knowledge schema v2.1 (non-destructive)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ai_lab.knowledge.json_knowledge import SCHEMA_VERSION
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def needs_migration(store: ProjectStore) -> bool:
    ver = store.root / "knowledge" / "schema_version.json"
    if not ver.exists():
        # Legacy flat claims index?
        if (store.root / "research" / "claims_index.json").exists():
            return True
        return False
    raw = json.loads(ver.read_text(encoding="utf-8"))
    return str(raw.get("schema_version")) != SCHEMA_VERSION


def migrate_project_knowledge(store: ProjectStore, *, legacy_run_id: str = "run_legacy_migrated") -> dict:
    """
    Non-destructive migration:
    - copies legacy research/*.json claims into .runs/<legacy_run_id>/claims/
    - leaves originals in place
    - writes knowledge/schema_version.json
    - writes knowledge/migration_report.json
    """
    warnings: list[str] = []
    migrated = 0
    (store.root / "knowledge").mkdir(parents=True, exist_ok=True)
    legacy_index = store.root / "research" / "claims_index.json"
    dest_dir = store.root / ".runs" / legacy_run_id / "claims"
    dest_dir.mkdir(parents=True, exist_ok=True)

    new_index: dict[str, str] = {}
    global_index: dict[str, dict] = {}

    if legacy_index.exists():
        index = json.loads(legacy_index.read_text(encoding="utf-8"))
        for claim_id, rel in index.items():
            src = store.root / rel
            if not src.exists():
                warnings.append(f"Missing legacy claim file: {rel}")
                continue
            raw = json.loads(src.read_text(encoding="utf-8"))
            raw.setdefault("project_id", store.name)
            raw.setdefault("run_id", legacy_run_id)
            raw.setdefault("lifecycle", "ARCHIVED")
            version = int(raw.get("version") or 1)
            dest_rel = f".runs/{legacy_run_id}/claims/{claim_id}_v{version}.json"
            dest = store.root / dest_rel
            if not dest.exists():
                dest.write_text(json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
                migrated += 1
            else:
                warnings.append(f"Skip existing migrated claim: {dest_rel}")
            new_index[claim_id] = dest_rel
            global_index[claim_id] = {
                "run_id": legacy_run_id,
                "path": dest_rel,
                "canonical_id": f"{store.name}/{legacy_run_id}/{claim_id}/v{version}",
                "version": str(version),
            }
        store.write_json(f".runs/{legacy_run_id}/claims/index.json", new_index)
        # Merge global index
        gpath = store.root / "knowledge" / "claims_global_index.json"
        existing = {}
        if gpath.exists():
            existing = json.loads(gpath.read_text(encoding="utf-8"))
        existing.update(global_index)
        store.write_json("knowledge/claims_global_index.json", existing)
    else:
        warnings.append("No legacy research/claims_index.json — nothing to copy")

    # Ensure graph dirs
    (store.root / "knowledge" / "graph").mkdir(parents=True, exist_ok=True)
    for name in ("nodes.json", "edges.json"):
        p = store.root / "knowledge" / "graph" / name
        if not p.exists():
            p.write_text("[]\n", encoding="utf-8")

    report = {
        "schema_version": SCHEMA_VERSION,
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "migrated_claims": migrated,
        "legacy_run_id": legacy_run_id,
        "warnings": warnings,
        "note": "Original research/ claim files were preserved (non-destructive).",
    }
    store.write_json("knowledge/migration_report.json", report)
    store.write_json(
        "knowledge/schema_version.json",
        {"schema_version": SCHEMA_VERSION, "migrated_at": report["migrated_at"]},
    )
    logger.info("Knowledge migration complete: %s claims, %s warnings", migrated, len(warnings))
    return report
