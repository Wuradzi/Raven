"""Explicit user-owned facts, never model-inferred memories or secrets."""
import re

from core.confirmation import ConfirmationPrompt
from core.models import SkillResult
from core.security import redact_user_text


def parse_request(text):
    text = text.strip().rstrip('.!?')
    text = re.sub(r"[’ʼ]", "'", text)
    if re.fullmatch(r"(?:що ти пам'ятаєш(?: про мене)?|покажи (?:всю )?пам'ять)", text, re.I):
        return 'list', ''
    if re.fullmatch(r"(?:забудь усе(?: про мене)?|очисти пам'ять)", text, re.I):
        return 'clear', ''
    match = re.fullmatch(r"(запам'ятай|онови(?: пам'ять)?|забудь)[,:\s]+(.+)", text, re.I)
    if not match:
        return None
    return ('remember' if match[1].lower() == "запам'ятай" else
            'forget' if match[1].lower() == 'забудь' else 'update'), match[2].strip()


_FORGET_LEAD = re.compile(
    r"^(?:(?:інформацію|дані|факти?|записи?)(?:\s+|$))?(?:з\s+(?:особистої\s+)?пам'яті(?:\s+|$))?(?:про(?:\s+|$))?",
    re.I)


def forget_query(content):
    """Topic of a forget request without filler words; empty when the user named no topic."""
    return _FORGET_LEAD.sub('', content.strip(), count=1).strip()


def matching_facts(items, query):
    """Facts whose key/value hold the phrase or every keyword stem of the topic («кафедру» → «кафедра»)."""
    from services.storage.knowledge_store import knowledge_terms
    phrase, stems = query.casefold(), knowledge_terms(query)
    if not stems:  # only stop words («про», «мене»): never match everything
        return []
    return [item for item in items
            if phrase in (item['key'] + ' ' + item['value']).casefold()
            or stems <= knowledge_terms(item['key'] + ' ' + item['value'])]


def forget_selection(store, operation, content):
    """(facts to forget, None) or (None, reply) when nothing may be forgotten yet."""
    if operation == 'clear':
        return store.all(), None
    query = forget_query(content)
    if not query:
        return None, SkillResult(True, 'Назвіть, що саме забути: «забудь про …».')
    items = matching_facts(store.all(), query)
    if not items:
        return None, SkillResult(True, 'Відповідних записів пам’яті не знайшов.')
    return items, None


def fact_text(key, value):
    """«тема: значення», or the fact once when it was saved without a separate topic."""
    return value if key.casefold() == value.casefold() else f"{key}: {value}"


async def handle_request(request, context, llm):
    operation, content = request
    store = context.services['memory']
    if (len(content) > 1000 or redact_user_text(content) != content or
            re.search(r'парол|password|passwd|secret|токен|token|api.?key|api.?ключ|приватн.{0,8}ключ', content, re.I)):
        return SkillResult(True, 'Секрети не зберігаю як звичайні факти пам’яті.', {'command_type': 'memory_rejected'})
    if operation == 'list':
        items = [item for item in store.all() if not item.get('sensitive')]
        # Live test 06.11.2026: «кафедра…: кафедра…» was read out twice.
        return SkillResult(True, '\n'.join(fact_text(i['key'], i['value']) for i in items[:20]) or
                           'Збережених фактів поки немає.', {'command_type': 'memory_list'})
    key = value = ''
    if operation in {'remember', 'update'}:
        content = re.sub(r'^що\s+', '', content, flags=re.I)
        pair = re.split(r'\s*:\s*|\s+це\s+', content, maxsplit=1)
        key, value = (pair if len(pair) == 2 else (content, content))
        if not key or not value:
            return SkillResult(True, 'Назвіть факт повністю: запам’ятай тема: значення.')
        existing = [item for item in store.all() if item['key'].casefold() == key.casefold()]
        if operation == 'update' and not existing:
            return SkillResult(True, 'Не знайшов точну тему. Скажіть «що ти пам’ятаєш», потім «онови тема: значення».')
        reset = bool(existing)
        description = f'Зберегти факт «{fact_text(key, value)}»'
    else:
        items, early = forget_selection(store, operation, content)
        if early is not None:
            return early
        reset = True
        description = 'Забути всі збережені факти' if operation == 'clear' else (
            'Забути записи: ' + '; '.join(item['key'] for item in items))
    if reset:
        description += '. Також почати новий контекст розмови; старі журнали й архіви залишаться'
    if not await context.confirm(ConfirmationPrompt(description, description, short_question=True)):
        return SkillResult(True, 'Зміни пам’яті скасовано.', {'command_type': 'memory_cancelled'})
    # Reset first: failure must not claim that old context was removed.
    if reset:
        llm.new_conversation()
    if operation in {'remember', 'update'}:
        store.remember(key, value, category='user_explicit')
    else:
        ids = {item['id'] for item in items}
        store.remove_ids(ids)
    return SkillResult(True, 'Пам’ять оновлено.' + (
        ' Починаємо новий контекст розмови. Журнали й архіви не видалено.' if reset else ''),
        {'command_type': 'memory_' + operation, 'memory_context_reset': reset})
