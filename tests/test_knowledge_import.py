"""Knowledge base phase 2: explicit import from data/knowledge/ (local design doc).

Read-only plan, confirmation before apply, sha256 updates, removal of vanished
or secret files, limits, no backups of replaced text, and cloud consent:
imported entries reach the model only with knowledge_base.cloud_files=true.
"""
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import ConfigError, default_config, validate_config
from core.models import TurnEnvelope
from services.storage import knowledge_import, knowledge_store
from services.storage.knowledge_import import chunk_text, plan_import
from services.storage.knowledge_store import KnowledgeStore
from skills.memory.knowledge import handle_knowledge_request, parse_knowledge_request
from tests.processor_gate_matrix import build

DEPARTMENT = "Кафедра кібербезпеки ЛНТУ розташована в корпусі 7, аудиторія 412."


def docx(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f"<root><p>{text}</p></root>")
    return buffer.getvalue()


class _Folder(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.data = Path(self._temp.name)
        self.folder = self.data / "knowledge"
        self.folder.mkdir()
        self.store = KnowledgeStore(self.data / "knowledge_base.json", import_folder=self.folder)

    def write(self, name, content):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def apply(self):
        plan = plan_import(self.folder, self.store.imported_files())
        return plan, self.store.apply_import(plan)


class ChunkTests(unittest.TestCase):
    def test_paragraphs_merge_up_to_limit_and_long_ones_split(self):
        self.assertEqual(chunk_text("Перший абзац.\n\nДругий абзац."), ["Перший абзац. Другий абзац."])
        long = ("Речення номер один. " * 200).strip()
        chunks = chunk_text(long + "\n\nКінець.", limit=300)
        self.assertTrue(all(len(chunk) <= 300 for chunk in chunks))
        self.assertEqual(" ".join(chunks).split(), (long + " Кінець.").split())
        self.assertEqual(chunk_text(" \n\n "), [])


class PlanAndApplyTests(_Folder):
    def test_new_unchanged_changed_and_removed_files(self):
        self.write("кафедра.txt", DEPARTMENT)
        self.write("нотатки/бібліотека.md", "Бібліотека працює до 18:00.")
        self.write("довідка.docx", docx("Деканат на першому поверсі."))
        plan, counts = self.apply()
        self.assertEqual(plan.new, {"кафедра.txt", "нотатки/бібліотека.md", "довідка.docx"})
        self.assertEqual(counts["added"], 3)
        entry = self.store.search("де кафедра кібербезпеки")[0]
        self.assertEqual(entry["source"], {"kind": "file", "name": "кафедра.txt", "folder": "knowledge", "chunk": 1})
        self.assertEqual(entry["title"], "кафедра")

        plan = plan_import(self.folder, self.store.imported_files())
        self.assertFalse(plan.has_changes)
        self.assertEqual(len(plan.unchanged), 3)

        self.write("кафедра.txt", DEPARTMENT.replace("412", "415"))
        (self.folder / "довідка.docx").unlink()
        plan, counts = self.apply()
        self.assertEqual((plan.changed, plan.removed), ({"кафедра.txt"}, {"довідка.docx"}))
        texts = " ".join(entry["text"] for entry in self.store.entries())
        self.assertIn("415", texts)
        self.assertNotIn("412", texts)
        self.assertNotIn("Деканат", texts)
        # Replaced and removed text must not survive in rotating backups.
        self.assertEqual(list(self.data.glob("knowledge_base.json.bak*")), [])

    def test_skipped_files(self):
        self.write("ключі.txt", "мій пароль 12345")
        self.write("скан.pdf", "%PDF")
        self.write(".приховане.txt", DEPARTMENT)
        self.write("порожнє.txt", "   ")
        # The size check runs before reading; keep the limit above the other fixtures.
        with patch.object(knowledge_import, "MAX_BYTES", 28):
            self.write("велике.txt", "x" * 29)
            plan = plan_import(self.folder, {})
        self.assertEqual(plan.skipped, {"ключі.txt": "secret", "скан.pdf": "unsupported",
                                        "порожнє.txt": "unreadable", "велике.txt": "too_large"})
        self.assertFalse(plan.has_changes)

    def test_previously_imported_file_that_gains_a_secret_is_removed(self):
        self.write("кафедра.txt", DEPARTMENT)
        self.apply()
        self.write("кафедра.txt", DEPARTMENT + " Пароль від Wi-Fi: 12345678")
        plan, _counts = self.apply()
        self.assertEqual(plan.removed, {"кафедра.txt"})
        self.assertEqual(self.store.entries(), [])

    def test_symlink_outside_folder_is_skipped(self):
        outside = self.data / "secret.txt"
        outside.write_text(DEPARTMENT, encoding="utf-8")
        try:
            os.symlink(outside, self.folder / "link.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need extra privileges on this platform")
        plan = plan_import(self.folder, {})
        self.assertEqual(plan.skipped, {"link.txt": "outside_folder"})

    def test_limits_skip_whole_files(self):
        self.write("а.txt", "перший текст")
        self.write("б.txt", "другий текст")
        with patch.object(knowledge_store, "MAX_ENTRIES", 1):
            plan, counts = self.apply()
        self.assertEqual(counts["added"], 1)
        self.assertEqual(list(plan.skipped.values()), ["limit"])

    def test_manual_entries_survive_import(self):
        manual = self.store.add("Кафедра", DEPARTMENT)
        self.write("кафедра.txt", DEPARTMENT)
        self.apply()
        (self.folder / "кафедра.txt").unlink()
        self.apply()
        self.assertEqual([entry["id"] for entry in self.store.entries()], [manual["id"]])


class UpdatePhraseTests(_Folder, unittest.IsolatedAsyncioTestCase):
    def context(self, approved=True):
        return SimpleNamespace(services={"knowledge": self.store}, confirm=AsyncMock(return_value=approved))

    def test_update_phrases(self):
        for text in ("Онови базу знань", "Онови базу знань з файлів.", "Імпортуй файли в базу знань"):
            with self.subTest(text=text):
                self.assertEqual(parse_knowledge_request(text), ("update", "", ""))

    async def test_missing_folder_is_created_with_instructions(self):
        self.folder.rmdir()
        result = await handle_knowledge_request(("update", "", ""), self.context())
        self.assertTrue(self.folder.is_dir())
        self.assertIn("data/knowledge", result.response)

    async def test_update_requires_confirmation_and_reports_counts(self):
        self.write("кафедра.txt", DEPARTMENT)
        self.write("ключі.txt", "token abcdef")
        denied = self.context(False)
        result = await handle_knowledge_request(("update", "", ""), denied)
        self.assertEqual(result.data["command_type"], "knowledge_cancelled")
        self.assertIn("нових файлів 1", denied.confirm.await_args.args[0])
        self.assertEqual(self.store.entries(), [])
        result = await handle_knowledge_request(("update", "", ""), self.context(True))
        self.assertIn("додано 1", result.response)
        self.assertIn("Пропущено файлів: 1 (секрети)", result.response)
        unchanged = self.context(True)
        result = await handle_knowledge_request(("update", "", ""), unchanged)
        self.assertIn("Змін у теці немає", result.response)
        unchanged.confirm.assert_not_awaited()

    async def test_list_groups_file_chunks_and_forget_warns_about_folder(self):
        self.write("кафедра.txt", DEPARTMENT)
        self.apply()
        listed = await handle_knowledge_request(("list", "", ""), self.context())
        self.assertEqual(listed.response, "У базі знань: файл кафедра.txt.")
        result = await handle_knowledge_request(("forget", "кафедра", ""), self.context(True))
        self.assertIn("Файл лишився в теці", result.response)


class CloudConsentTests(_Folder, unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, consent):
        processor, _router, llm, _memory, _speaker = build("chat", True)
        processor.services["knowledge"] = self.store
        processor.settings.knowledge_cloud_files = consent
        envelope = TurnEnvelope(turn_id="kb", session_id="kb", source="text", text="Де кафедра кібербезпеки?",
                                transcript="Де кафедра кібербезпеки?", stt_engine="text", confidence=1.0)
        await processor.process(envelope, AsyncMock())
        return processor, llm.converse.await_args.kwargs.get("knowledge_context")

    async def test_imported_entries_need_explicit_consent(self):
        self.write("кафедра кібербезпеки.txt", DEPARTMENT)
        self.apply()
        _processor, sent = await self.run_turn(False)
        self.assertIsNone(sent)
        processor, sent = await self.run_turn(True)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("кафедра кібербезпеки.txt", json.dumps(sent, ensure_ascii=False))  # no file name
        source = await processor.process(TurnEnvelope(
            turn_id="kb2", session_id="kb", source="text", text="Звідки ти це знаєш?",
            transcript="Звідки ти це знаєш?", stt_engine="text", confidence=1.0), AsyncMock())
        self.assertIn("файл «кафедра кібербезпеки.txt»", source.response)


class ConfigTests(unittest.TestCase):
    def test_consent_defaults_to_false_and_must_be_boolean(self):
        config = default_config()
        self.assertIs(config["knowledge_base"]["cloud_files"], False)
        validate_config(config)
        config["knowledge_base"]["cloud_files"] = "yes"
        with self.assertRaises(ConfigError):
            validate_config(config)
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(example["knowledge_base"]["cloud_files"], False)


if __name__ == "__main__":
    unittest.main()
