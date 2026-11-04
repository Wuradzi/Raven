"""Local knowledge base: user reference texts found by keywords (docs/KNOWLEDGE_BASE.md).

Separate from personal memory. Entries are untrusted data for conversation,
never instructions or tool arguments. Source and date are kept for every entry
but are not given to the model; Raven reports them only on request.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.atomic_json import AtomicJSONFile
from core.command_intent import SENSITIVE_REQUEST
from core.security import redact_user_text

MAX_TITLE = 120
MAX_TEXT = 1500
MAX_ENTRIES = 5000
MAX_TOTAL_CHARS = 20 * 1024 * 1024
_SECRET = re.compile(r"парол|password|passwd|secret|токен|token|api.?key|api.?ключ|приватн.{0,8}ключ", re.I)


def contains_secret(text: str) -> bool:
    """Secrets never enter the knowledge base, from a phrase or from a file."""
    return bool(SENSITIVE_REQUEST.search(text) or _SECRET.search(text) or redact_user_text(text) != text)
SEARCH_LIMIT = 3
MIN_SCORE = 2  # two body stems, or one title stem (title stems count double)
STEM = 6
_STOP = {
    "розкажи", "поясни", "знайди", "знайти", "пошукай", "скажи", "покажи", "мені", "тобі", "мене", "тебе",
    "про", "для", "що", "як", "який", "яка", "яке", "які", "якого", "хто", "коли", "чому", "де", "чи",
    "це", "цей", "ця", "ці", "так", "там", "тут", "його", "її", "їх", "вона", "воно", "вони", "він",
    "був", "була", "було", "були", "буде", "наш", "ваш", "все", "усе", "дуже", "можна", "треба", "будь",
    "ласка", "рейвен", "raven", "the", "and", "what", "about",
}


def knowledge_terms(text: str) -> set[str]:
    """Keyword stems: casefold, words of 3+ letters, first 6 letters, no stop words."""
    words = re.findall(r"[\w'’]+", text.casefold())
    return {word[:STEM] for word in words if len(word) >= 3 and word not in _STOP and not word.isdigit()}


class KnowledgeError(ValueError):
    pass


def _file_entries(name: str, digest: str, chunks: list[str]) -> list[dict]:
    """Entries for one imported file: title from the file name, source kept locally."""
    now = datetime.now(timezone.utc).isoformat()
    stem = Path(name).stem[:MAX_TITLE - 20] or name[:MAX_TITLE - 20]
    result = []
    for index, chunk in enumerate(chunks, 1):
        title = stem if len(chunks) == 1 else f"{stem}, частина {index}"
        result.append({
            "id": f"kb_{uuid.uuid4().hex[:12]}",
            "title": title,
            "text": chunk,
            "terms": sorted(knowledge_terms(title + " " + chunk)),
            "source": {"kind": "file", "name": name, "folder": "knowledge", "chunk": index},
            "file_sha256": digest,
            "added_at": now,
            "updated_at": now,
        })
    return result


class KnowledgeStore:
    def __init__(self, path: Path, import_folder: Path | None = None):
        self.file = AtomicJSONFile(path, {"entries": []})
        self.import_folder = import_folder

    def imported_files(self) -> dict[str, str]:
        """name -> sha256 of every imported file currently in the base."""
        return {entry["source"]["name"]: entry.get("file_sha256", "") for entry in self.entries()
                if entry.get("source", {}).get("kind") == "file"}

    def apply_import(self, plan) -> dict[str, int]:
        """Apply a confirmed ImportPlan atomically; files over the limits are skipped."""
        state = self.file.load()
        replaced = plan.removed | plan.changed
        entries = [entry for entry in state["entries"]
                   if not (entry.get("source", {}).get("kind") == "file" and entry["source"]["name"] in replaced)]
        total = sum(len(entry["text"]) for entry in entries)
        added = updated = 0
        for name in sorted(plan.files):
            digest, chunks = plan.files[name]
            if len(entries) + len(chunks) > MAX_ENTRIES or total + sum(map(len, chunks)) > MAX_TOTAL_CHARS:
                plan.skipped[name] = "limit"
                continue
            entries.extend(_file_entries(name, digest, chunks))
            total += sum(map(len, chunks))
            added, updated = (added, updated + 1) if name in plan.changed else (added + 1, updated)
        state["entries"] = entries
        # Replaced or removed file text must not survive in rotating backups (J128).
        self.file.save(state, forget_history=bool(replaced))
        return {"added": added, "updated": updated, "removed": len(plan.removed), "skipped": len(plan.skipped)}

    def entries(self) -> list[dict]:
        return self.file.load()["entries"]

    def add(self, title: str, text: str) -> dict:
        title, text = " ".join(title.split()), text.strip()
        if not title or not text or len(title) > MAX_TITLE or len(text) > MAX_TEXT:
            raise KnowledgeError("invalid entry size")
        state = self.file.load()
        if len(state["entries"]) >= MAX_ENTRIES:
            raise KnowledgeError("knowledge base is full")
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": f"kb_{uuid.uuid4().hex[:12]}",
            "title": title,
            "text": text,
            "terms": sorted(knowledge_terms(title + " " + text)),
            "source": {"kind": "manual", "name": "фраза користувача"},
            "added_at": now,
            "updated_at": now,
        }
        state["entries"].append(entry)
        self.file.save(state)
        return entry

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
        wanted = knowledge_terms(query)
        if not wanted:
            return []
        scored = []
        for entry in self.entries():
            score = len(wanted & set(entry["terms"])) + 2 * len(wanted & knowledge_terms(entry["title"]))
            if score >= MIN_SCORE:
                scored.append((score, entry["updated_at"], entry))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for _score, _updated, entry in scored[:limit]]

    def matching(self, topic: str) -> list[dict]:
        """Entries whose title holds the phrase or every topic stem («кафедру» → «кафедра»)."""
        phrase = " ".join(topic.casefold().split())
        stems = knowledge_terms(topic)
        return [entry for entry in self.entries() if phrase and (
            phrase in entry["title"].casefold() or (stems and stems <= knowledge_terms(entry["title"])))]

    def get(self, ids) -> list[dict]:
        wanted = list(ids)
        by_id = {entry["id"]: entry for entry in self.entries()}
        return [by_id[item] for item in wanted if item in by_id]

    def remove_ids(self, ids: set[str]) -> int:
        state = self.file.load()
        before = len(state["entries"])
        state["entries"] = [entry for entry in state["entries"] if entry["id"] not in ids]
        # Forgotten reference text must not survive in rotating backups (J128).
        self.file.save(state, forget_history=True)
        return before - len(state["entries"])

    def clear(self) -> None:
        self.file.save({"entries": []}, forget_history=True)
