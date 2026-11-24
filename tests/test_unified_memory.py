"""Одне локальне сховище: факти (items) і довідки (entries) в data/memory.json."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.storage.unified_memory import migrate_legacy_knowledge, open_memory
from skills.memory.personal import handle_request


class SharedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_both_collections_live_in_one_file_and_survive_each_other(self):
        memory, knowledge = open_memory(self.data)
        memory.remember("керівник", "Гринюк")
        entry = knowledge.add("Бібліотека", "Бібліотека ЛНТУ працює до шостої вечора.")
        saved = json.loads((self.data / "memory.json").read_text(encoding="utf-8"))
        self.assertEqual([i["key"] for i in saved["items"]], ["керівник"])
        self.assertEqual([e["id"] for e in saved["entries"]], [entry["id"]])
        self.assertFalse((self.data / "knowledge_base.json").exists())
        memory.clear()  # очищення фактів не торкається довідок
        self.assertEqual(memory.all(), [])
        self.assertEqual(len(knowledge.entries()), 1)
        memory.remember("група", "КІм-21")
        knowledge.clear()  # і навпаки
        self.assertEqual(knowledge.entries(), [])
        self.assertEqual([i["key"] for i in memory.all()], ["група"])

    def test_old_memory_file_without_entries_still_loads(self):
        (self.data / "memory.json").write_text(json.dumps({"items": [{"id": "mem_1", "key": "k", "value": "v",
            "category": "general", "created_at": "", "updated_at": "", "sensitive": False}]}), encoding="utf-8")
        memory, knowledge = open_memory(self.data)
        self.assertEqual(knowledge.entries(), [])
        self.assertEqual(len(memory.all()), 1)
        knowledge.add("Тема", "Текст довідки.")
        self.assertEqual(len(memory.all()), 1)

    def test_deleting_forgets_history_for_both_collections(self):
        memory, knowledge = open_memory(self.data)
        memory.remember("секретний-на-вигляд", "значення для видалення")
        knowledge.add("Видалю", "текст для видалення")
        memory.remember("ще", "факт")  # створює резервну копію
        self.assertTrue(list(self.data.glob("memory.json.bak*")))
        knowledge.remove_ids({e["id"] for e in knowledge.entries()})
        self.assertEqual(list(self.data.glob("memory.json.bak*")), [])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.legacy = {"entries": [
            {"id": "kb_1", "title": "Кафедра", "text": "Кафедра комп'ютерної інженерії.", "terms": ["кафедр"],
             "source": {"kind": "manual", "name": "фраза користувача"}, "added_at": "", "updated_at": ""},
            {"id": "kb_2", "title": "Файл", "text": "Текст файла.", "terms": ["файл"],
             "source": {"kind": "file", "name": "a.md", "folder": "knowledge", "chunk": 1}, "added_at": "",
             "updated_at": ""}]}
        (self.data / "knowledge_base.json").write_text(json.dumps(self.legacy), encoding="utf-8")

    def test_legacy_knowledge_is_merged_once_and_the_old_file_is_kept_renamed(self):
        memory, knowledge = open_memory(self.data)
        self.assertEqual({e["id"] for e in knowledge.entries()}, {"kb_1", "kb_2"})
        self.assertFalse((self.data / "knowledge_base.json").exists())
        kept = self.data / "knowledge_base.json.migrated"
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8")), self.legacy)
        again_memory, again_knowledge = open_memory(self.data)  # повторний запуск нічого не дублює
        self.assertEqual(len(again_knowledge.entries()), 2)

    def test_entries_already_present_are_skipped_and_facts_are_kept(self):
        memory, _ = open_memory(self.data)
        memory.remember("керівник", "Гринюк")
        (self.data / "knowledge_base.json.migrated").unlink(missing_ok=True)
        from core.atomic_json import AtomicJSONFile
        shared = AtomicJSONFile(self.data / "memory.json", {"items": [], "entries": []})
        self.assertEqual(migrate_legacy_knowledge(self.data, shared), 0)  # старого файла вже немає
        (self.data / "knowledge_base.json").write_text(json.dumps(self.legacy), encoding="utf-8")
        self.assertEqual(migrate_legacy_knowledge(self.data, shared), 0)  # id вже є: пропущено
        self.assertEqual(len(shared.load()["entries"]), 2)
        self.assertEqual([i["key"] for i in shared.load()["items"]], ["керівник"])


class UnifiedListTests(unittest.TestCase):
    def test_list_phrase_shows_facts_and_reference_titles_together(self):
        with tempfile.TemporaryDirectory() as temp:
            memory, knowledge = open_memory(Path(temp))
            memory.remember("керівник", "Гринюк")
            knowledge.add("Бібліотека", "Бібліотека ЛНТУ працює до шостої вечора.")
            context = SimpleNamespace(services={"memory": memory, "knowledge": knowledge}, confirm=AsyncMock())
            result = asyncio.run(handle_request(("list", ""), context, None))
            self.assertIn("керівник: Гринюк", result.response)
            self.assertIn("Довідки: Бібліотека.", result.response)
            self.assertEqual(result.data["command_type"], "memory_list")


if __name__ == "__main__":
    unittest.main()
