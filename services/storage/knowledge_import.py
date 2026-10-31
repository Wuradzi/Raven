"""Knowledge base phase 2: plan an import from the local folder (data/knowledge/).

Read-only: the plan is shown to the user and applied by KnowledgeStore only
after confirmation. No background scanning, no symlinks, no files outside the
folder, no secrets. Same formats and size limit as the document reader.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from services.files.document_context import MAX_BYTES, SUPPORTED, DocumentError, extract_document
from services.storage.knowledge_store import MAX_TEXT, contains_secret

MAX_FILES = 500


@dataclass
class ImportPlan:
    files: dict[str, tuple[str, list[str]]] = field(default_factory=dict)  # name -> (sha256, chunks)
    new: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    unchanged: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)  # name -> reason code

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed or self.removed)


def chunk_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Paragraph chunks up to `limit` characters; long paragraphs split by sentences."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        while len(paragraph) > limit:
            cut = max(paragraph.rfind(". ", 0, limit), paragraph.rfind(" ", 0, limit))
            cut = cut + 1 if cut > limit // 2 else limit
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _inspect(path: Path, folder: Path, existing: dict[str, str]):
    """(name, status, payload) for one file; status is a reason code when skipped."""
    name = path.relative_to(folder).as_posix()
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        return name, "outside_folder", None
    if path.suffix.lower() not in SUPPORTED:
        return name, "unsupported", None
    if path.stat().st_size > MAX_BYTES:
        return name, "too_large", None
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if existing.get(name) == digest:
        return name, "unchanged", None
    try:
        text = extract_document(data, path.suffix.lower())
    except DocumentError:
        return name, "unreadable", None
    if contains_secret(text):
        return name, "secret", None
    chunks = chunk_text(text)
    return (name, "ready", (digest, chunks)) if chunks else (name, "empty", None)


def _files(folder: Path):
    for root, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if not name.startswith("."):
                yield Path(root) / name


def plan_import(folder: Path, existing: dict[str, str]) -> ImportPlan:
    """Compare the folder with imported files (`existing`: name -> sha256)."""
    plan = ImportPlan()
    for index, path in enumerate(_files(folder)):
        if index >= MAX_FILES:
            plan.skipped[path.relative_to(folder).as_posix()] = "limit"
            continue
        try:
            name, status, payload = _inspect(path, folder, existing)
        except OSError:
            plan.skipped[path.relative_to(folder).as_posix()] = "unreadable"
            continue
        if status == "unchanged":
            plan.unchanged.add(name)
        elif status == "ready":
            plan.files[name] = payload
            (plan.changed if name in existing else plan.new).add(name)
        else:
            plan.skipped[name] = status
    # A file that vanished or became unreadable / secret loses its old entries.
    plan.removed = set(existing) - plan.unchanged - set(plan.files)
    return plan
