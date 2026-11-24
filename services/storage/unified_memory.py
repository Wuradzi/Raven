"""One local memory file: facts about the user (items) and reference entries (entries).

`data/memory.json` holds both collections; MemoryStore and KnowledgeStore are views over the same
AtomicJSONFile (one lock, one backup history). The former `data/knowledge_base.json` is merged in once
and renamed, never deleted.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.atomic_json import AtomicJSONFile
from services.storage.knowledge_store import KnowledgeStore
from services.storage.memory_store import MemoryStore

logger = logging.getLogger(__name__)
LEGACY_KNOWLEDGE = "knowledge_base.json"
MIGRATED_SUFFIX = ".migrated"


def migrate_legacy_knowledge(data_dir: Path, shared: AtomicJSONFile) -> int:
    """Merge data/knowledge_base.json into the shared file; returns the number of entries added.

    The merged state is saved first, then the old file is renamed to *.migrated (kept as a backup).
    Running it again finds no old file and does nothing; entries already present (same id) are skipped.
    """
    legacy = data_dir / LEGACY_KNOWLEDGE
    if not legacy.exists():
        return 0
    old = AtomicJSONFile(legacy, {"entries": []}).load().get("entries", [])
    state = shared.load()
    state.setdefault("items", [])
    present = state.setdefault("entries", [])
    known = {entry.get("id") for entry in present}
    added = [entry for entry in old if entry.get("id") not in known]
    state["entries"] = present + added
    shared.save(state)
    target = legacy.with_name(LEGACY_KNOWLEDGE + MIGRATED_SUFFIX)
    if target.exists():
        target = legacy.with_name(f"{LEGACY_KNOWLEDGE}{MIGRATED_SUFFIX}.{len(list(data_dir.glob(LEGACY_KNOWLEDGE + '.migrated*')))}")
    legacy.rename(target)
    logger.info("memory.migration knowledge_entries_added=%s", len(added))
    return len(added)


def open_memory(data_dir: Path) -> tuple[MemoryStore, KnowledgeStore]:
    """(facts store, reference store) over data/memory.json, after the one-time migration."""
    shared = AtomicJSONFile(data_dir / "memory.json", {"items": [], "entries": []})
    migrate_legacy_knowledge(data_dir, shared)
    return (MemoryStore(data_dir / "memory.json", file=shared),
            KnowledgeStore(data_dir / "memory.json", import_folder=data_dir / "knowledge", file=shared))
