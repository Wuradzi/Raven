"""Explicit knowledge-base phrases: add, list, forget, clear, update. Changes need confirmation."""
import asyncio
import re

from core.confirmation import ConfirmationPrompt
from core.models import SkillResult
from services.storage.knowledge_store import MAX_TEXT, MAX_TITLE, KnowledgeError, contains_secret

_ADD = re.compile(r"(?:додай (?:у|в|до) баз[иу] знань|запам'ятай як довідку)[,:\s]+(.+)", re.I | re.S)
_LIST = re.compile(r"(?:що (?:є )?(?:у|в) базі знань|покажи базу знань)", re.I)
_FORGET = re.compile(r"(?:забудь|видали) довідку(?: про)?\s+(.+)", re.I)
_CLEAR = re.compile(r"(?:очисти|видали) базу знань", re.I)
_UPDATE = re.compile(r"(?:онови|оновити|імпортуй файли (?:у|в|до)) баз[уи] знань(?: з файлів)?", re.I)
IMPORT_FOLDER_LABEL = "data/knowledge"
_SKIP_REASONS = {
    "secret": "секрети", "unsupported": "непідтримуваний формат", "too_large": "більше 2 МіБ",
    "unreadable": "не прочитано", "empty": "без тексту", "outside_folder": "посилання за межі теки",
    "limit": "ліміт бази",
}


def parse_knowledge_request(text):
    """(operation, title, body) or None; never matches personal-memory phrases."""
    value = re.sub(r"[’ʼ]", "'", text.strip()).rstrip(".!?")
    match = _ADD.fullmatch(value)
    if match:
        content = match[1].strip()
        title, sep, body = content.partition(":")
        return ("add", title.strip(), body.strip()) if sep and body.strip() else ("add", content[:60], content)
    for operation, pattern in (("list", _LIST), ("clear", _CLEAR), ("update", _UPDATE)):
        if pattern.fullmatch(value):
            return (operation, "", "")
    match = _FORGET.fullmatch(value)
    return ("forget", match[1].strip(), "") if match else None


async def _confirmed(context, description):
    return await context.confirm(ConfirmationPrompt(description, description, short_question=True))


def _cancelled():
    return SkillResult(True, "Зміни бази знань скасовано.", {"command_type": "knowledge_cancelled"})


async def handle_knowledge_request(request, context):
    operation, title, body = request
    store = context.services["knowledge"]
    if operation == "list":
        return _list(store)
    if operation == "add":
        return await _add(store, title, body, context)
    if operation == "forget":
        return await _forget(store, title, context)
    if operation == "update":
        return await _update(store, context)
    if not await _confirmed(context, "Очистити всю базу знань"):
        return _cancelled()
    had_files = bool(store.imported_files())
    store.clear()
    note = " Файли в теці лишилися: «Онови базу знань» імпортує їх знову." if had_files else ""
    return SkillResult(True, "Базу знань очищено." + note, {"command_type": "knowledge_clear"})


def _list(store):
    labels = []
    for entry in store.entries():
        source = entry.get("source", {})
        label = f"файл {source['name']}" if source.get("kind") == "file" else entry["title"]
        if label not in labels:
            labels.append(label)
    return SkillResult(True, ("У базі знань: " + "; ".join(labels[:20]) + ".") if labels else
                       "База знань поки порожня.", {"command_type": "knowledge_list"})


async def _add(store, title, body, context):
    if contains_secret(title + " " + body):
        return SkillResult(True, "Секрети не зберігаю в базі знань.", {"command_type": "knowledge_rejected"})
    if len(title) > MAX_TITLE or len(body) > MAX_TEXT:
        return SkillResult(True, f"Довідка задовга: тема до {MAX_TITLE}, текст до {MAX_TEXT} символів.",
                           {"command_type": "knowledge_rejected"})
    if not await _confirmed(context, f"Додати в базу знань довідку «{title}»"):
        return _cancelled()
    try:
        store.add(title, body)
    except KnowledgeError:
        return SkillResult(True, "Не вдалося додати довідку: база знань заповнена або запис порожній.",
                           {"command_type": "knowledge_rejected"})
    return SkillResult(True, f"Додано в базу знань: «{title}».", {"command_type": "knowledge_add"})


async def _forget(store, topic, context):
    entries = store.matching(topic)
    if not entries:
        return SkillResult(True, "Не знайшов довідок із такою темою.", {"command_type": "knowledge_forget"})
    titles = "; ".join(entry["title"] for entry in entries[:10])
    if not await _confirmed(context, f"Видалити з бази знань: {titles}"):
        return _cancelled()
    count = store.remove_ids({entry["id"] for entry in entries})
    from_files = any(entry.get("source", {}).get("kind") == "file" for entry in entries)
    note = " Файл лишився в теці: видаліть його, інакше «Онови базу знань» поверне довідку." if from_files else ""
    return SkillResult(True, f"Видалено довідок: {count}." + note, {"command_type": "knowledge_forget"})


async def _update(store, context):
    from services.storage.knowledge_import import plan_import
    folder = store.import_folder
    if folder is None:
        return SkillResult(True, "Імпорт файлів недоступний у цій сесії.", {"command_type": "knowledge_update"})
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        return SkillResult(True, f"Створив теку {IMPORT_FOLDER_LABEL}. Покладіть туди файли й скажіть "
                           "«Онови базу знань».", {"command_type": "knowledge_update"})
    # Reading up to hundreds of documents must not block the voice/asyncio loop.
    plan = await asyncio.to_thread(plan_import, folder, store.imported_files())
    skipped = _skipped_summary(plan.skipped)
    if not plan.has_changes:
        return SkillResult(True, "Змін у теці немає." + skipped, {"command_type": "knowledge_update"})
    description = (f"Оновити базу знань з теки {IMPORT_FOLDER_LABEL}: нових файлів {len(plan.new)}, "
                   f"змінених {len(plan.changed)}, видалених {len(plan.removed)}")
    if not await _confirmed(context, description):
        return _cancelled()
    counts = store.apply_import(plan)
    return SkillResult(True, f"Базу знань оновлено: додано {counts['added']}, оновлено {counts['updated']}, "
                       f"видалено {counts['removed']} файлів." + _skipped_summary(plan.skipped),
                       {"command_type": "knowledge_update", **counts})


def _skipped_summary(skipped):
    if not skipped:
        return ""
    reasons = sorted({_SKIP_REASONS.get(code, code) for code in skipped.values()})
    return f" Пропущено файлів: {len(skipped)} ({', '.join(reasons)})."
