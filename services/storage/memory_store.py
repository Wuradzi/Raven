from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.atomic_json import AtomicJSONFile


class MemoryStore:
    """Факти про користувача (колекція items). Може ділити файл із KnowledgeStore (колекція entries)."""

    def __init__(self, path: Path, file: AtomicJSONFile | None = None):
        self.file = file if file is not None else AtomicJSONFile(path, {"items": []})

    def _state(self) -> dict:
        state = self.file.load()
        state.setdefault("items", [])
        return state

    def remember(self, key: str, value: str, category: str = "general") -> dict:
        state = self._state()
        now = datetime.now(timezone.utc).isoformat()
        for item in state["items"]:
            if item["key"].lower() == key.lower():
                item.update(value=value, category=category, updated_at=now)
                self.file.save(state)
                return item

        item = {
            "id": f"mem_{uuid.uuid4().hex[:12]}",
            "key": key.strip(),
            "value": value.strip(),
            "category": category,
            "created_at": now,
            "updated_at": now,
            "sensitive": False,
        }
        state["items"].append(item)
        self.file.save(state)
        return item

    def find(self, query: str) -> list[dict]:
        words = set(re.findall(r"[\w'’]+", query.lower()))
        scored = []
        for item in self._state()["items"]:
            haystack = f"{item['key']} {item['value']} {item['category']}".lower()
            score = sum(word in haystack for word in words)
            if score:
                scored.append((score, item))
        return [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)]

    def relevant(self, query: str, limit: int = 5) -> list[dict]:
        return self.find(query)[:limit]

    def forget(self, query: str) -> int:
        state = self._state()
        before = len(state["items"])
        state["items"] = [
            item for item in state["items"]
            if query.lower() not in item["key"].lower()
        ]
        self.file.save(state, forget_history=True)
        return before - len(state["items"])

    def all(self) -> list[dict]:
        return self._state()["items"]

    def remove_ids(self, ids: set[str]) -> None:
        state = self._state()
        state['items'] = [item for item in state['items'] if item['id'] not in ids]
        self.file.save(state, forget_history=True)

    def clear(self) -> None:
        state = self._state()
        state["items"] = []  # колекцію довідок спільного файлу не чіпаємо
        self.file.save(state, forget_history=True)
