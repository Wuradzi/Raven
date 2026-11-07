# ValleRa: behavioral contracts, Phase 1

## Доповнення 07.11.2026: керування комп'ютером (рішення користувача)

- Нові ACTION-інструменти моделі: `media_control` (volume_up/down з кроками 1–10, mute_toggle,
  play_pause, next/previous_track), `show_desktop`, `lock_screen`, `screenshot`; `window_control`
  отримав `focus` і `close`. Аргументи — лише закриті значення (`core/command_intent.py`).
- Без підтвердження (`UNCONFIRMED_TOOLS`): `media_control`, `show_desktop`; `window_control focus`.
  З корельованим підтвердженням: `window_control close` (і, як раніше, maximize/minimize/restore),
  `lock_screen`, `screenshot`. Закриття працює з тим самим знімком вікна й перевіркою «те саме вікно»,
  що й інші зміни вікон; програма може відмовитися закритися — тоді «не підтверджено», без повтору.
- Медіаклавіші й Win+D: відповідь «надіслав команду», `verified=false` («запит передано ≠ виконано»).
  Знімок екрана лишається локально (Зображення\Raven) і не передається моделі.
- Голос Groq без confidence може запропонувати ті самі інструменти (`UNSCORED_VOICE_TOOLS`), через
  ту саму політику прямого прохання, тип запиту (`request_scope`) і виконавця. Пошкоджене мовлення
  не виконує нічого (знімок `tests/turn_pipeline_snapshot.json`, 1260 випадків).
- Виконавець має явний обробник для кожного інструмента; інструмент без обробника відхиляється.
  Раніше будь-який непередбачений інструмент потрапляв у вебпошук.
- Завершення процесів і вимкнення комп'ютера моделі не доступні.
- Живий тест 07.11.2026 (E282): `media_control` приймає відсутній `steps` (= 1) і ціле число замість
  тексту; решта інструментів без змін. Закриття вікна розрізняє «вікна більше немає» і «вікно
  сховано» (програма в треї, наприклад Spotify: «Вікно сховано: програма могла лишитися працювати
  в треї»), а видиме вікно лишається «не підтверджено» (програма питає про збереження). Пряме
  прохання приймає «виключи звук», «тихіше / тише / гучніше», «продовжуй відтворення».

## Доповнення 07.11.2026 (roadmap фаза 1, крок 1): один фільтр і рівень довіри

- Один фільтр відповіді моделі (`core/reply_screen.py`) на обох шляхах ходу. Раніше шлях голосу
  Groq без confidence озвучував «Зменшую гучність.», «UNSUPPORTED» та інші заяви про дію без
  заміни. Тепер, як і на шляху з оцінкою, звучить «Дію ще не виконано. Для запуску потрібен
  погоджений план.», а в історію пишеться те, що прозвучало. У режимі звичайної розмови —
  одне повідомлення «Я не виконую локальні дії в розмові…» (два однакові за змістом злито).
- `TrustLevel` (`core/turn_gate.py`): SCORED / UNSCORED_CLEAN / DAMAGED — мітка для маршрутизації,
  не дозвіл. Процесор більше не читає прапорці STT з конверта напряму; умови вибору пропозиції й
  кандидата Groq не змінились. Виклик моделі (фраза очікування, скасування) спільний для обох шляхів.
- Оракул: `tests/turn_pipeline_snapshot.json` — 504 випадки «довіра × тип відповіді моделі ×
  репліка». Змінилися лише 96 випадків заяв про дію (текст і озвучення); виконання й підтвердження — ні.
- Живий тест 07.11.2026: відповідь без заголовка «UNSUPPORTED\n<пояснення>» озвучувалась цілою.
  Тепер рядки-токени протоколу на початку відповіді чи частини потоку не озвучуються й не
  пишуться в історію; пояснення після них проходить звичайний фільтр. Відповідь лише з токена
  — повідомлення «Дію ще не виконано…», один раз. Знімок — 588 випадків.

## Доповнення 06.11.2026 (roadmap фаза 4): швидкість без зміни очікування тиші

- Очікування кінця фрази (endpoint) не змінювалось: темп мовлення різний (рішення користувача).
- `llm.first_text_timeout_seconds` (5 с; 0 вимикає): якщо модель не дала **жодного** тексту за
  цей час, той самий хід передається наступному доступному провайдеру. Так само й при тайм-ауті,
  помилці сервера чи зв'язку або ліміті запитів до першого тексту. Почута, декодована чи збережена
  відповідь не повторюється: після першого тексту хід не перемикається. Заблокована, невалідна
  чи нерозібрана відповідь у резерв не йде, щоб інша модель не отримала другого шансу на дію.
  Резервна відповідь проходить ті самі фільтри й політику дозволів, бо резерв лише відповідає,
  а виконання й підтвердження лишаються локальними. Без резерву чекає, як раніше, у межах
  загального дедлайну. Наступний хід знову починає з основного провайдера.
- Нестримінговий `chat` таймаутом першого фрагмента не обрізається.
- Голосом під час вебпошуку чи пошуку файлів, якщо робота триває понад 7 с, Raven один раз
  каже «Ще шукаю.». Час очікування підтвердження не враховується; це статус, не результат.

## Доповнення 06.11.2026 (roadmap фаза 7): пам'ять і база знань

- «Звідки ти це знаєш?» локально називає й факти особистої пам'яті, передані моделі в
  попередньому ході, але лише ті, що мають спільне ключове слово з попередньою відповіддю.
  Модель отримує особисту пам'ять із позначкою «факти, які користувач сам зберіг» і правилом
  не називати їх загальновідомими.
- «Що є в базі данних?» (правопис STT) — перелік бази знань, не вебпошук.
- Відмова від пошуку при knowledge gap: «Пошук … не запускаю» лише коли користувач відмовився
  від пошуку або в запиті чутливі дані; інакше — лише «Не знаю цього напевно.».
- Факт пам'яті без окремої теми показується й підтверджується один раз, без «тема: тема».
- Типові псевдоніми програм: «блокнот» → Notepad, «пейнт» → Paint.
- Фраза очікування «Хвилинку.» (`llm.filler_seconds`) діє й на шляху голосу Groq без confidence
  (`_restricted_turn`), а не лише на шляху з оцінкою; раніше там вона не спрацьовувала.
- STT-написання «Відкрай …» ремонтується в «Відкрий …» (`voice_request`) і для шляху Groq.
- Автоматичний вебпошук при knowledge gap потребує щонайменше двох ключових слів у репліці:
  «Точно?», «Серйозно?», «Невже?» пошуку не запускають.
- «Забудь інформацію з особистої пам'яті про …» знімає службові слова; тема шукається за основами слів
  («кафедру» → «кафедра»); без теми або лише зі стоп-словами нічого не видаляється й Raven просить
  назвати тему (раніше порожній запит міг збігтися з усіма фактами).

## Доповнення 06.11.2026: вибір голосом Groq і галюцинації Whisper

- Чиста репліка Groq без confidence (не unreliable, не обрізана, не обірвана) може
  вибрати пункт із власної пропозиції Raven (`TaskContext`: «так», «ні», номер) —
  та сама планка, що й для голосового підтвердження Groq. Лише запропоновані пункти,
  один раз, у межах TTL; перевірки stamp файлу й підтвердження ризикових типів
  лишаються. Інструменти чи команди така репліка не виконує.
- Цілі транскрипти-галюцинації Whisper («Дякую за перегляд!», «Продовження слідує…»
  тощо) відкидаються як `subtitle_hallucination`, як і чужомовні; ті самі слова
  всередині довшої фрази зберігаються.
- Фільтр заяв про дію також ловить «зменшую / збільшую / вмикаю / вимкнула /
  перемикаю / налаштовую …» на початку фрази; голий службовий токен відповіді
  (`UNSUPPORTED`, `CHAT`, `LOOKUP` тощо) не озвучується. Фільтр заяв про пам'ять
  охоплює й «ім'я».

## Доповнення 04.11.2026 (варіант A оцінки проєкту): менше тертя

Рішення користувача після оцінки проєкту (`docs/PROJECT_ASSESSMENT.md`, локальний):
- Без підтвердження виконуються лише `web_search`, `weather`, `open_app`
  (`UNCONFIRMED_TOOLS` у `core/command_actions.py`); вебпошук спершу локально
  озвучує точний запит. `find_files`, `window_control`, `prepare_workplace` і всі
  зміни файлів, пам'яті, бази знань, системи й pentest — з корельованим
  підтвердженням, як раніше. Політика дозволу (direct_request, scope, заперечення,
  секрети, кілька дій, assistant_offer, proposal lease) не змінювалась.
- Це стосується й голосу Groq без confidence: свідомо прийнято, що неправильно
  почутий запит може піти в пошук або відкрити програму з локального каталогу.
  Підтвердження і далі не підвищують довіри до STT.
- `LOOKUP` / knowledge gap: замість «Не знаю цього напевно» і запиту «так» —
  «Шукаю в інтернеті «…».» і пошук одразу; фраза пишеться в історію. Відмова від
  пошуку, секрети й не-інформаційні репліки, як і раніше, блокують пошук.
  Промпт: загальновідоме модель пояснює сама, LOOKUP — свіжі, нішеві чи невідомі факти.
- Спотворені цілі команди бази знань без аргументів («О нову базу знань»,
  «Що я в базі знань?») розпізнаються як план оновлення (з підтвердженням) або
  перелік (лише читання). «Онови / Очисти базу даних» і далі не змінюють нічого.
- `llm.filler_seconds`: одне «Хвилинку.» в голосовій репліці, якщо модель мовчить
  довше заданого; не в історії, не відповідь і не підтвердження.
- Синоніми згоди не розширено (J57), крім раніше доданого «давай».

## Доповнення 04.11.2026 (третя сесія): база знань без згоди на хмару

- Якщо знайдені довідки з файлів, а `knowledge_base.cloud_files` вимкнено, модель
  отримує лише лічильник `withheld_file_entries`: без назви, id чи тексту. Правило
  велить сказати, що вміст недоступний, не вигадувати його й не називати відповідь
  записом бази знань. Джерело й дата, як і раніше, лишаються локальними.
- Питання про джерело («Звідки ти це знаєш?») приймає початок «і / й / та / ну / а».
  Локально відповідає лише за довідками, що були передані моделі.
- «База даних» приймається лише для читання переліку («Що є в базі даних?»).
  Зміни (додати, видалити, очистити, оновити) потребують точного «базу знань» і
  підтвердження.
- Ввічливі форми «Забудьте / Видаліть довідку…», «Очистіть / Оновіть базу знань»
  розпізнаються й так само потребують підтвердження.

## Доповнення 04.11.2026 (друга сесія): підтвердження й заяви про дані

- Фільтр заяв моделі також охоплює «дані» і «базу даних» («оновив дані»,
  «оновив базу даних»), а не лише пам'ять і базу знань.
- Голос Groq без confidence: лексична підказка незавершеності (фраза закінчується,
  наприклад, на «хочу») не вважається пошкодженням. Така відповідь може лише
  відмовити («Не хочу»), але ніколи не підтверджує. Пошкоджений або обрізаний запис
  не відмовляє й не підтверджує.
- «Давай» — згода лише як уся відповідь (зокрема повторена); з іншими словами
  («давай не будемо», «не хочу, давай») відповідь лишається нерозпізнаною.
  Голосова граматика Vosk не розширена.

## Доповнення 04.11.2026: виправлення після живого тесту бази знань

- Текст моделі, що стверджує зміну пам'яті чи бази знань («збережено», «забуто»,
  «додав довідку» тощо поруч зі словами пам'ять / база знань / довідка / факт /
  запис / інформація), замінюється повідомленням «Нічого не змінено …» з точними
  фразами. Змінюють пам'ять і базу лише локальні фрази з підтвердженням.
- «Не знаю цього напевно» записується в історію розмови як відповідь Raven на обох
  шляхах (звичайному й голосу Groq без confidence).
- Підтвердження: відмовою також є відповідь, що починається з «ні» або «не треба /
  не потрібно / не хочу / не шукай / не роби / не відкривай / не запускай / не згоден»,
  якщо в ній немає слова згоди. Позитивні форми не розширено; змішане лишається unknown.
- «Забудь довідку про …» шукає тему за основами слів (відмінки), видалення — лише
  після підтвердження з переліком тем.

## Доповнення 31.10.2026: локальна база знань (фаза 1)

- Окреме сховище довідок користувача (`data/knowledge_base.json`), не особиста пам'ять.
  Додавання, видалення й очищення — лише явними фразами й після підтвердження;
  секрети відхиляються; видалення прибирає `.bakN`.
- Знайдені за ключовими словами записи передаються моделі як недовірені дані
  користувацького рівня, без джерела й дати. Для прямого запиту дії (`direct_request`)
  база не передається: вона не може стати аргументом інструмента.
- «Звідки ти це знаєш?» відповідає локально з записів останньої відповіді, не модель.
- Хмарній моделі йдуть лише записи, додані користувачем; записи з файлів — лише
  після окремої згоди `knowledge_base.cloud_files: true` (за замовчуванням `false`).
- Фаза 2 (31.10.2026): імпорт лише з `data/knowledge/` і лише за командою «Онови базу
  знань». План змін читається поза asyncio-циклом без запису і застосовується атомарно
  лише після підтвердження. Символічні посилання й шляхи поза текою, файли із секретами,
  непідтримувані й більші за 2 МіБ пропускаються; файл, що зник або отримав секрет,
  втрачає свої записи; замінений і видалений текст не лишається в `.bakN`.

## Доповнення 31.10.2026: прогалина знань → запропонований вебпошук

- Модель не вигадує фактів. Якщо конкретних фактів немає в її знаннях, вона
  повертає `LOOKUP` (один рядок пошукового запиту; дані, не дія) або непрямий
  ACTION `web_search`. Обидва йдуть маршрутом `knowledge_gap`.
- Маршрут діє лише для `web_search` і лише для інформаційного запиту (питання чи
  «розкажи/поясни/знайди…»). Не діє за відмови від пошуку («не шукай», «без
  інтернету»), секретів у репліці чи запиті та кількох дій; для інших інструментів
  не діє ніколи.
- Raven каже «Не знаю цього напевно.», далі виконавець показує точний запит і питає
  звичайне корельоване підтвердження. Без «так» нічого не виконується.
- Запит формулює модель (може виправити спотворену STT назву); користувач бачить
  його в підтвердженні. Прямий запит «Знайди …» і далі зберігає слова користувача.
- Однаково для текстового/оціненого шляху й для голосу Groq без confidence
  (`_groq_candidate_action`). Той самий шлях тепер застосовує `final_request` і
  `assistant_offer` (J131), чого раніше бракувало для Groq.

## Доповнення 30.10.2026 — J131: контекст природних запитів

- Прямий gate оцінює останнє речення, якщо вступ розмовний (`final_request`): без
  дієслів дії, умов, переказу, цитат і чутливих даних. Інакше — уся репліка.
  Модель і далі отримує повну репліку; підтвердження виконавця незмінне.
- Пошуковий запит лишається словами користувача, без «інформацію про» та кінцевої
  пунктуації; модель не замінює назви чи написання.
- Новий вузький виняток із «просте «так» не дозволяє дії»: ствердна відповідь на
  так/ні-пропозицію Raven того самого типу дії (останнє речення попередньої відповіді)
  допускає ACTION моделі до виконавця (`intent_route=assistant_offer`). Виконавець
  показує точні аргументи і питає підтвердження; лише web_search, find_files, open_app,
  weather; питання-уточнення («Яку програму відкрити?») пропозицією не є.

## Phase 2 — 29.09.2026, J61

Актуальний command transport описано у [архітектурі](ARCHITECTURE.md).
Phase 1.5 семипольовий tuple замінено frozen `TurnEnvelope`. Черга відкидає
всі 3/4/5/6/7 positional форми, не має legacy tuple adapter. Dispatcher claim-ить
існуючий operational ticket, processor не створює нового ID. ID збережений
також у CommandContext; diagnostic timing ID не перейменовано.

Source/text/transcript/engine/confidence та independent flags передаються явно.
`clarification_required` навмисно не прирівнюється до `utterance_incomplete`:
завершена, але ненадійно розпізнана дія і раніше могла вимагати повтору.
Старий RecognitionResult.incomplete лишається compatibility repeat signal;
listener додатково зберігає semantic incompleteness до unsafe-refinement guard.
Не переоцінюється STT policy і не змінюється Fragment Guard.

Збережено cancellation, stale microphone epoch, confirmation correlation,
одноразовий dispatch, scoped selection, confidence thresholds і ACTION gates.
Envelope immutable, але існуючий timing reference закономірно лишається mutable.
Відсутні confirmation/clarification IDs та microphone token усередині envelope:
на цій boundary вони не потрібні, перевірки лишаються у чинних власників.

Тести: 320/320 targeted (E126), 575/575 full (E127), офлайн. Додано real-processor
ID propagation, незалежність flags, immutable snapshot, equivalence старого
action gate та відхилення tuple input. Це не новий live STT або GitHub CI run.
Для наступної фази: погодити видалення legacy RecognitionResult flags/string
adapter та подальше розділення відповідальностей; у Phase 2 це не реалізовано.

## Доповнення Phase 1.5.1 — 27.09.2026, J57

Voice confirmation передає незмінений transcript і confidence окремо у submit;
request-ID check Phase 1.5 незмінений. _decision виконує exact semantic mapping
у True/False/None; voice confidence <0.8 або nonfinite/out-of-range дає UNKNOWN.
UI/text confidence=None зберігає попередню семантику. UNKNOWN повторює питання,
не продовжуючи початковий deadline. Негативні форми доповнено «не треба»,
«скасуй», «відмовляюсь»; позитивні не розширено.

Restricted grammar отримує доступний у model vocabulary unknown symbol:
[unk], <UNK> або <unk>, перший наявний. Локальна українська модель має <UNK>.
«ні» було і залишається у grammar та words.txt (ні=926054, так=1411087,
гаразд=287129). Перекодування/alias так→гаразд у parser/capture не знайдено.
Немає доказу точної акустичної причини live misrecognition без відповідного PCM.

Лише для canonical confirmation grammar energy window 80 ms замість 250 ms:
80 ms тихого сигналу розводилися тишею і могли не пройти RMS threshold. Це
відтворено синтетичним fixture, не доведено як причина конкретного live «ні».
Normal gate залишається 250 ms; native endpoint, Whisper bypass, routing і
Fragment Guard незмінні. Логи: redacted recognized text, engine, confidence,
semantic, перші 8 символів request ID; grammar і energy rejection reason.
Unknown branch/threshold не гарантують відсутності high-confidence помилки Vosk.
E120/E121 — офлайн тести, не перевірка акустичної якості живого confirmation.

## Доповнення Phase 1.5 — 26.09.2026, J56

Нижче збережено історичний baseline Phase 1. Дві його відкриті межі тепер
закриті на orchestration boundary, без TurnEnvelope:

- ConfirmationService.submit(text, request_id) приймає лише поточний непорожній ID.
  Voice захоплює ID перед STT; web draft — при першому input event; Windows
  console — коли перша клавіша доступна, перед стандартним input(). ID проходить
  через текстову чергу/API без повторного прив'язування. Stale reply відкидається,
  не стає новою командою. UI-кнопки зберігають перевірку ID та bool.
- Для redirected/non-Windows stdin ID знімається перед blocking read: якщо
  confirmation виник/змінився за цей час, відповідь не підтверджує дію. Може
  знадобитися повторне введення або UI-кнопка. Немає speaker identity гарантії.
- _enqueue_command видає одноразовий operational turn ID: runtime nonce +
  monotonic sequence. Новий queue tuple має 7 полів:
  (text, source, confidence, timing, fragmented, incomplete, operational_turn_id).
  Це НЕ TurnTiming.turn_id: останній залишається діагностичним ID без зміни PERF.
  Повторна доставка зберігає operational ID; нова реальна репліка отримує новий.
- _command_loop атомарно claim-ить ticket перед processor. Duplicate, retired,
  foreign або unissued ID відкидається з логом та task_done. Старі queue tuples
  без ticket більше не є дозволеним ingress; усі production producers оновлено.
- DispatchGuard зберігає лише outstanding tickets (максимум 64), не історію
  всіх turn. Claim видаляє ticket; старий ID не перевидається навіть після
  тисяч нових turn. Capacity overflow fail-closed, без eviction і false matches
  за transcript. Cancelled enqueue видаляє ticket, shutdown очищає pending.
  Повторний виклик ingress для нової репліки — новий ID, не retry старого item.

Гарантія — at-most-once processor dispatch виданого operational ID у runtime,
не exactly-once зовнішній ефект і не захист від довільної зміни внутрішнього Python
state. Confirmation/direct_request/verification, mic epochs, Fragment Guard та
STT policy не послаблено. E118: 310/310; E119: 563/563, лише офлайн fixtures.
Windows keyboard та browser draft lifecycle потребують живого smoke test.

## Історичний baseline Phase 1

Дата фіксації: 26.09.2026. Це опис поточного коду, не нова policy.
Перевірки — офлайн fixtures та підставні виконавці; не live STT/LLM/Windows QA.
Повний TurnEnvelope/TurnContext у цій фазі не вводиться.

## Межа гарантій

Один результат одного штатного capture має не більше одного enqueue до
command_queue. Confirmation capture має нуль command dispatch: відповідь
потрапляє лише в ConfirmationService. Empty/conflict/stale capture не dispatch-иться.
Це НЕ глобальна exactly-once/idempotency гарантія: queue не дедуплікує елементи
за TurnTiming.turn_id. Подвійне ручне enqueue однакового tuple викликає processor
двічі. Це зафіксовано characterization-тестом, а не оголошено бажаною поведінкою.
Повторне розпізнавання тієї самої акустичної фрази як двох capture також не
розпізнається тут як дубль. End-to-end operational turn identity ще відсутня.

## Інваріанти й наявне покриття

| Контракт | Фактичне правило | Тести, які вже існували до Phase 1 |
|---|---|---|
| CHAT | Natural CHAT не є дозволом інструмента; текст проходить decoder | tester.py: NaturalTurnChecks.test_plain_fallback_and_valid_dialogue_at_every_chunk_split; test_hallucinated_action_from_mention_or_context_is_blocked_locally |
| CLARIFY | Уточнення не виконує дію; scope створює локальний processor, не довільний текст LLM | NaturalTurnChecks.test_app_name_followup_requires_fresh_confirmation_and_uses_literal_name; test_yes_negation_and_expired_followups_never_launch |
| ACTION | Модель пропонує дані; потрібні direct_request/scope, validate_intent, enabled skill, чинні approvals | tests/test_command_intent.py: test_forged_intent_is_revalidated_at_executor_boundary; test_proposal_is_confirmed_before_execution; NaturalTurnChecks.test_prefix_free_open_waits_for_confirmation_and_records_actual_result |
| Confirmation | Відмова/скасування не означає approval; finally очищає request/queue | test_denial_never_executes; test_cancellation_while_speaking_clears_confirmation_and_never_executes; test_failed_confirmation_speech_clears_state |
| Scoped reply | Назва походить із поточної репліки; TTL, explicit scope, нове confirmation | NaturalTurnChecks.test_short_stt_reply_keeps_existing_scoped_clarification; tests/test_audio_recognition.py: test_scoped_hint_uses_existing_pending_context_and_catalogue |
| Selection | Файл/app/window вибирається з локальних candidates, одноразово; перевірка актуальності | tests/test_task_context.py: test_select_second_file_is_local_and_one_shot; test_expired_choice_never_executes; test_apps_are_rechecked_against_index |
| Fragment | allow_actions=False перевіряється перед споживанням selection; incomplete дає local repeat | NaturalTurnChecks.test_fragmented_request_never_executes_or_consumes_pending_selection; test_fragmented_explicit_command_requires_fresh_request |
| STT uncertainty | Потрібний, але невдалий refinement не дає права ACTION; flags зберігаються | tests/test_audio_recognition.py: test_empty_or_unreliable_refinement_never_authorizes_action; test_timeout_chat_fallback_and_action_repeat |
| Late Whisper | Mailbox старої роботи не dispatch-ить; busy не запускає паралельну transcription | test_busy_cancelled_and_late_results_do_not_start_parallel_jobs; tests/test_audio_lifecycle.py: test_bounded_wait_drains_old_pipe_reply_and_reuses_same_process |
| Interruption | Epoch відкидає старий capture; speaker generation — стару озвучку; tools не undo-яться | tests/test_audio_lifecycle.py: test_text_reply_starting_during_capture_discards_audio; tester.py: DialogueChecks.test_stop_invalidates_blocked_and_late_speech_but_accepts_new_turn |
| Execution verification | accepted/submitted не дорівнює verified success; непідтверджений результат не спричиняє автоматичний повтор | tester.py: WorkplaceChecks.test_launched_but_unverified_is_partial_no_retry; test_process_without_window_stops_and_does_not_relaunch |
| Endpoint | Native/quiet paths, silence, bounded grace, merge та interrupt збережені | tests/test_audio_continuation.py, зокрема test_interrupt_at_final_endpoint_wins_over_dispatch і test_capture_deadline_does_not_dispatch_unfinished_action |

Ці правила не означають, що всі legacy local skills мають однакове confirmation.
Не змінювати їхню семантику під виглядом уніфікації. Висока STT confidence не є
доказом семантичної правильності, авторизацією користувача або voice identity.

## Додані тести Phase 1

У tests/test_audio_lifecycle.py, VoiceTurnContractTests:

1. test_one_capture_enqueues_and_dispatches_once_preserving_guard — production
   voice/command loops, один enqueue і один process; timing і обидва flags збережені.
2. test_empty_and_conflicting_stt_have_no_dispatch — zero dispatch.
3. test_low_confidence_voice_never_reaches_processor — low/NaN confidence не
   викликає processor чи confirmation.
4. test_stale_ui_confirmation_id_cannot_approve_current_action — obsolete UI ID дає 409.
5. test_confirmation_voice_reply_is_not_a_command_dispatch — голосове «ні» йде
   в confirmation, а не command queue.
6. test_queue_consumer_supports_all_existing_tuple_shapes — 3/4/5/6 полів.
7. test_stale_capture_is_discarded_before_new_turn — thread barrier, зміна epoch,
   late capture відкинуто, наступний turn оброблений лише один раз.
8. test_transport_has_no_global_turn_id_deduplication — документує відсутність
   replay protection при штучному повторному enqueue, без реальних дій.

У tests/test_command_intent.py, IntentExecutionTests:

9. test_cancelled_approval_cannot_be_reused_by_next_action — queued «так» під час
   озвучення + cancellation; новий request ID та порожня queue; наступна відмова
   не відкриває програму.
10. test_submission_is_not_verified_success_and_is_not_retried — accepted=True,
    verified=False лишається success=False; запуск рівно один раз.

Тести підтверджують конкретні interleavings/межі, не всі можливі scheduling races.

## Фактичний transport listen → app → processor

### RecognitionResult (core/models.py)

Поля: text, confidence, engine, optional timing, fragmented=False,
incomplete=False, capture_truncated=False. Mutable dataclass slots.
timing не бере участі у compare/repr. Confidence Vosk — середнє word confidence.
Для engine=conflict app просить повторити, не enqueue-ить. Порожній текст
пропускається. Interrupted listener повертає порожній текст.

`fragmented` зараз перевантажений: означає не лише акустичний fragment, а й
tool-free fallback після невдалого refinement. `capture_truncated` окремо
зберігає timeout capture; fragment flags переоцінюються за final selected text.

### Positional tuples

| Межа | Поля за позицією | Власник / споживач |
|---|---|---|
| Audio callback queue | (raw_pcm_bytes, callback_perf_counter) | _listen_on_device; локальна черга capture |
| Capture result | (RecognitionResult, captured_pcm_bytes) | _listen_on_device → listen_once |
| Refinement wait | (RecognitionResult, outcome, elapsed_ms) | RefinementWait → listen_once |
| Whisper IPC request | (operation, args) | ProcessWhisperRecognizer → _worker |
| Whisper IPC reply | (result, status, last_duration_seconds, timings) | _worker → _request; consumer підтримує legacy 3 поля без timings |
| Text command queue | (text, source, confidence) | _submit_text; source=text, confidence=1.0 |
| Normal voice queue | (text, source, confidence, timing) | _voice_loop; source=voice |
| Guarded voice queue | (text, source, confidence, timing, True, incomplete) | _voice_loop |
| Legacy guarded form | (text, source, confidence, timing, guard) | _command_loop приймає; поточний production producer не знайдено |
| Speech context | (speaker, generation) | SPEECH_SCOPE; не operational turn identity |

У queue index 4 — guard, НЕ allow_actions: truthy означає передати
allow_actions=False; index 5 — incomplete_input, без нього False.
timing=None/відсутній → app створює TurnTiming на dispatch. Engine і
capture_truncated окремими полями в queue не передаються; PCM далі не йде.
repair_voice_text виконується перед enqueue лише якщо fragmented=False.

_command_loop перевіряє voice confidence перед process. Для explicit local
prefix/pentest/selection використовує command threshold; для інших реплік —
chat threshold. Далі виклик:
process(text, confirmation.ask, source, [allow_actions=False, incomplete_input=...]).
Confidence і engine не є полями CommandContext. Не підміняти STT confidence
модельною впевненістю intent під час наступного cleanup.

### Стан і ownership

- QuietEndpoint: partial, native/quiet evidence, held/merged, continuation deadline.
- VoskListener: model/device, interrupt/paused/closed events; один capture result.
- RefinementWait: один drain-only job і приватний mailbox; жодного dispatch callback.
- ProcessWhisperRecognizer: pipe/process, request/state locks, model status.
- ValleRaApp: command queue, command_idle, microphone epoch, _voice_inflight.
- TurnTiming: metric turn_id і milestones; не cancellation/authorization token.
- CommandProcessor: _natural_pending з expires/tool/request/question та для window
  window_action; conversational state і поточний tool-free LLM task.
- TaskContext: pending Selection(kind, entries, expires_at, stamps).
- ConfirmationService: request_id, prompt, response queue, events і lock.
- CommandContext: settings/services/confirm callback/source/raw/normalized text.
- NaturalTurn: kind/response/intent; ACTION draft — не execution receipt.
- SkillResult: handled/response/data; data включає accepted/verified/success/status
  залежно від інструмента, не всі fields присутні в кожному result.
- Speaker: SpeechRequest(text, queued_at, timing, generation), playback generation.

## Cancellation та межі safety

Скасоване ask очищає накопичені відповіді; «так» без активного ask відхиляється.
UI confirmation перевіряє request_id. Voice/text submit(text) не має request ID
у payload: відповідь прив'язується до поточного awaiting request. Тому глобальна
гарантія проти довільно запізнілого «так», що прийшло вже після старту іншого ask,
не доведена. Не плутати її з протестованим очищенням старої queue при cancellation.

Late Whisper результат лише дочитується; capture epoch та SpeechRequest generation
захищають різні межі. asyncio cancellation не undo-ить уже виконаний OS side effect.
Зупинка TTS не є відмовою від усіх інструментів. Interruption tool-free conversation
не повинне автоматично скасовувати/повторювати локальні операції.

## Майбутній TurnEnvelope — лише план

Перенести: operational id від capture до dispatch, source, raw/final transcript,
engine/confidence, окремі reliability reason і action eligibility, fragment/truncation
evidence, timing, cancellation generation; references на clarification/confirmation IDs.
Queue має нести один типізований об'єкт; ownership transitions мають бути явними.
Окремо спроєктувати at-most-once consumption за ID та прив'язку confirmation response
до конкретного action proposal. Не переносити до envelope services, process handles,
повний history чи автоматично чинний approval. Workflow/confirmation лишаються
окремими owners; intent/dispatch receipt не тотожні transcript.

## Перевірка

Єдина точка входу: tester.py.
Targeted: `python tester.py --module stt commands runtime performance --timeout 180`.
Full: `python tester.py --all --timeout 180`.
Фактичні результати й hashes — J55 та E116/E117 у реєстрі доказів.
Єдина production зміна Phase 1 — annotation command_queue (3/4/5/6 полів) і коментар;
tuple construction/consumption та всі runtime branches залишилися незмінними.
