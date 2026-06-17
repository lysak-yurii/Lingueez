# Lingueez — Ukrainian (uk) translations.
# Keys are English UI strings; values are their Ukrainian equivalents.

TRANSLATIONS: dict[str, str] = {
    # ── Common ─────────────────────────────────────────────────────────────
    "Cancel": "Скасувати",
    "OK": "OK",
    "Close": "Закрити",
    "Save": "Зберегти",
    "Delete": "Видалити",
    "Edit": "Редагувати",
    "Remove": "Видалити",
    "Add": "Додати",
    "Refresh": "Оновити",
    "Import": "Імпорт",
    "Export": "Експорт",
    "Search": "Пошук",
    "Fetch": "Завантажити",
    "Browse…": "Огляд…",
    "Clear": "Очистити",
    "Pause": "Пауза",
    "Resume": "Продовжити",
    "Language": "Мова",
    "Translation": "Переклад",
    "Word": "Слово",
    "Status": "Статус",
    "Error": "Помилка",
    "Title": "Заголовок",
    "Topic": "Тема",
    "Level": "Рівень",
    "Generate": "Генерувати",
    "Generating…": "Генерується…",
    "Translating…": "Перекладається…",
    "Format": "Формат",
    "Style": "Стиль",
    "Model": "Модель",
    "Font": "Шрифт",
    "Usage": "Використання",
    "Translation language": "Мова перекладу",

    # ── main_window.py ──────────────────────────────────────────────────────
    "Menu": "Меню",
    "Open Excel Table…": "Відкрити таблицю Excel…",
    "Import Excel to Database…": "Імпортувати Excel до бази даних…",
    "Save Import Template…": "Зберегти шаблон імпорту…",
    "PDF…": "PDF…",
    "Excel / CSV…": "Excel / CSV…",
    "TXT…": "TXT…",
    "Audio (MP3)…": "Аудіо (MP3)…",
    "Backups…": "Резервні копії…",
    "Show Source column": "Показати стовпець «Джерело»",
    "Show Created At column": "Показати стовпець «Дата створення»",
    "Max words…": "Максимум слів…",
    "View Log": "Переглянути журнал",
    "About": "Про програму",
    "Quit": "Вийти",
    "Words": "Слова",
    "Texts": "Тексти",
    "Statistics": "Статистика",
    "Bin (deleted items)": "Кошик (видалені елементи)",
    "Settings": "Налаштування",
    "Vocabulary": "Словник",
    "Search words, translations or tags…": "Пошук слів, перекладів або тегів…",
    "Search texts by title, content or words…": "Пошук текстів за заголовком, вмістом або словами…",
    "Search scope": "Область пошуку",
    "Add word": "Додати слово",
    " Favorites": " Обрані",
    "Filter by tag": "Фільтрувати за тегом",
    "Close file and return to your vocabulary": "Закрити файл та повернутися до словника",
    "Definition": "Визначення",
    "Read": "Читати",
    "Favorite": "Обране",
    "Tags": "Теги",
    "Copy": "Копіювати",
    "Text": "Текст",
    "Delete selected (Del)": "Видалити вибране (Del)",
    "No data": "Немає даних",
    "No texts yet": "Ще немає текстів",
    "Words: {shown}/{total}": "Слова: {shown}/{total}",
    "Texts: {total}": "Тексти: {total}",
    "Texts: {shown}/{total}": "Тексти: {shown}/{total}",
    "{count} selected": "{count} вибрано",
    "No selection": "Нічого не вибрано",
    "Please select at least one word.": "Будь ласка, виберіть хоча б одне слово.",
    "Saved": "Збережено",
    "'{word}' updated.": "«{word}» оновлено.",
    "Database Error": "Помилка бази даних",
    "Delete {count} word(s)?": "Видалити {count} слово(слів)?",
    "Deleted": "Видалено",
    "{count} word(s) deleted.": "{count} слово(слів) видалено.",
    "Deleted with {n} error(s).": "Видалено з {n} помилкою(помилками).",
    "Favorites": "Обрані",
    "{count} word(s) added to favorites.": "{count} слово(слів) додано до обраних.",
    "{count} word(s) removed from favorites.": "{count} слово(слів) видалено з обраних.",
    "Status set to '{status}' for {count} word(s).": "Статус «{status}» встановлено для {count} слово(слів).",
    "Max Words": "Максимум слів",
    "Show only the first N words (0 = show all):": "Показати лише перші N слів (0 = показати всі):",
    "View Definition": "Переглянути визначення",
    "Copy Word": "Копіювати слово",
    "Copy Translation": "Копіювати переклад",
    "Toggle Favorite": "Змінити «Обране»",
    "Change Status…": "Змінити статус…",
    "Add / Remove Tags…": "Додати / видалити теги…",
    "Read Aloud": "Читати вголос",
    "Change Status": "Змінити статус",
    "New status:": "Новий статус:",
    "Copied": "Скопійовано",
    "{count} row(s) copied to clipboard.": "{count} рядок(рядків) скопійовано до буфера.",
    "{count} item(s) copied to clipboard.": "{count} елемент(елементів) скопійовано до буфера.",
    "Copy Word(s)": "Копіювати слово(а)",
    "Copy Translation(s)": "Копіювати переклад(и)",
    "Copy Both": "Копіювати обидва",
    "Search in Word": "Шукати в слові",
    "Search in Translation": "Шукати в перекладі",
    "Search in Tags": "Шукати в тегах",
    "Promoted": "Просунуто",
    "Google Cloud TTS unavailable": "Google Cloud TTS недоступний",
    "Selection limit": "Ліміт вибору",
    "Only the first 200 selected words will be read.": "Буде прочитано лише перші 200 вибраних слів.",
    "Only the first 50 words will be used.": "Буде використано лише перші 50 слів.",
    "Select words to save as audio.": "Виберіть слова для збереження як аудіо.",
    "Nothing to export.": "Немає нічого для експорту.",
    "Export Error": "Помилка експорту",
    "Settings saved.": "Налаштування збережено.",
    "Generated text saved.": "Згенерований текст збережено.",
    "Show": "Показати",
    "Add Word": "Додати слово",
    "Stop reading": "Зупинити читання",
    "Read — Read selected words aloud": "Читати — Прочитати вибрані слова вголос",
    "Translation": "Переклад",

    # ── settings_dialog.py ─────────────────────────────────────────────────
    "Appearance": "Вигляд",
    "Audio": "Аудіо",
    "Learning": "Вивчення",
    "System": "Система",
    "Light": "Світла",
    "Dark": "Темна",
    "Appearance mode": "Режим вигляду",
    "Widget scaling": "Масштаб елементів",
    "Table size": "Розмір таблиці",
    "Interface language": "Мова інтерфейсу",
    "Restart the app to apply the language change.": "Перезапустіть додаток для зміни мови.",
    "TTS provider": "Постачальник TTS",
    "Google Cloud credentials": "Облікові дані Google Cloud",
    "Voice type": "Тип голосу",
    "Voice name (optional)": "Назва голосу (необов'язково)",
    "Read Aloud playback": "Налаштування читання вголос",
    "Pause between words (s)": "Пауза між словами (с)",
    "Repeats per word": "Повторень на слово",
    "Repeats per pair": "Повторень на пару",
    "Promote status while listening": "Підвищувати статус під час прослуховування",
    "Listens to reach {status}": "Прослуховувань для «{status}»",
    "Excel import": "Імпорт Excel",
    "Placeholder values": "Значення-замінники",
    "Skip placeholder rows": "Пропускати рядки-замінники",
    "Skip empty rows": "Пропускати порожні рядки",
    "Normalize language pairs": "Нормалізувати мовні пари",
    "How to import": "Як імпортувати",
    "Save import template…": "Зберегти шаблон імпорту…",
    "Active provider": "Активний постачальник",
    "API key": "Ключ API",
    "API URL": "URL API",
    "Check usage": "Перевірити використання",
    "Enable cloud sync": "Увімкнути хмарну синхронізацію",
    "Supabase URL (.env)": "URL Supabase (.env)",
    "Supabase key (.env)": "Ключ Supabase (.env)",
    "Bin cleanup grace (days)": "Термін у кошику (дні)",
    "Test Connection": "Перевірити з'єднання",
    "Restart the app after enabling sync for the first time.": "Перезапустіть додаток після першого увімкнення синхронізації.",
    "Start automatically on login (minimized to tray)": "Запускати автоматично при вході (у згорнутому вигляді)",
    "Add Word hotkey (global)": "Гаряча клавіша «Додати слово» (глобальна)",
    "Data format": "Формат даних",
    "Columns to export": "Стовпці для експорту",
    "Sheet name": "Назва аркуша",
    "Start row": "Початковий рядок",
    "Start column": "Початковий стовпець",
    "Shade alternate rows": "Затінити чергувальні рядки",
    "Auto column width": "Автоматична ширина стовпців",
    "Freeze header row": "Закріпити рядок заголовка",
    "Delimiter": "Роздільник",
    "Delimiter (\\t = tab)": "Роздільник (\\t = таб)",
    "Include header lines": "Включити рядки заголовка",
    "Header lines": "Рядки заголовка",
    "Page size": "Розмір сторінки",
    "Font size": "Розмір шрифту",
    "Line spacing (pt)": "Міжрядковий інтервал (pt)",
    "Text alignment": "Вирівнювання тексту",
    "Margins L/R/T/B (pt)": "Відступи Л/П/В/Н (pt)",
    "Automatic widths (fit page)": "Автоматична ширина (до сторінки)",
    "Columns / width": "Стовпці / ширина",
    "Header background": "Фон заголовка",
    "Header text": "Текст заголовка",
    "Row background": "Фон рядка",
    "Grid lines": "Лінії сітки",
    "Background image": "Фонове зображення",
    "Concurrent workers": "Паралельні виконавці",
    "Requests per second": "Запитів на секунду",
    "Add font…": "Додати шрифт…",
    "Page && text": "Сторінка та текст",
    "Columns": "Стовпці",
    "Max tokens": "Максимум токенів",
    "Temperature": "Температура",
    "Prompt template": "Шаблон запиту",
    "Definitions": "Визначення",
    "Generated Texts (from words)": "Генеровані тексти (зі слів)",
    "Generated Texts (by topic)": "Генеровані тексти (за темою)",
    "Text Adaptation (to level)": "Адаптація тексту (до рівня)",
    "Thinking budget (0 = off, -1 = auto)": "Бюджет мислення (0 = вимкнено, -1 = авто)",

    # ── add_word.py ────────────────────────────────────────────────────────
    "Detect language": "Визначити мову",
    "Type a word or phrase…": "Введіть слово або фразу…",
    "Translation…": "Переклад…",
    "Pronounce": "Вимовити",
    "Swap word and translation": "Поміняти місцями слово та переклад",
    "Translate with DeepL (Enter)": "Перекласти через DeepL (Enter)",
    "Save Word": "Зберегти слово",
    "Enter a word to translate.": "Введіть слово для перекладу.",
    "Source equals target — translated to {lang} instead.": "Мова джерела збігається з цільовою — перекладено на {lang}.",
    "Both word and translation are required.": "Потрібні обидва: слово та переклад.",
    "Please select the source language before saving.": "Будь ласка, виберіть мову джерела перед збереженням.",
    "'{word}' already exists in your dictionary.": "«{word}» вже є у вашому словнику.",
    "The text was truncated to the first 100 words.": "Текст скорочено до перших 100 слів.",

    # ── definition.py ──────────────────────────────────────────────────────
    "Show translation's definition": "Показати визначення перекладу",
    "Show word's definition": "Показати визначення слова",
    "Generate with AI": "Згенерувати за допомогою ШІ",
    "Regenerate with AI": "Перегенерувати за допомогою ШІ",
    "Definition 2": "Визначення 2",
    "No definition stored yet. Use \"Generate with AI\" or \"Edit\" to add one.": "Визначення ще не додано. Використайте «Згенерувати за допомогою ШІ» або «Редагувати».",
    "There is no word to define.": "Немає слова для визначення.",
    "API key missing": "Відсутній ключ API",
    "Set your {ai} API key in Settings → APIs → AI first.": "Спочатку вкажіть ключ {ai} у Налаштуваннях → APIs → AI.",
    "Generating definition…": "Генерується визначення…",

    # ── tags.py ────────────────────────────────────────────────────────────
    "Tags — {count} word(s)": "Теги — {count} слово(слів)",
    "New tag name…": "Нова назва тегу…",
    "Add Tag": "Додати тег",
    "Apply Selected to All": "Застосувати вибране до всіх",
    "Remove Selected": "Видалити вибрані",
    "(partial)": "(часткове)",
    "use(s)": "вжитків",
    "Tags marked ✓ apply to all selected words.": (
        "Теги з ✓ застосовуються до всіх вибраних слів."
    ),
    "◐ (partial) means only some of them have the tag.": (
        "◐ (часткове) — тег є лише у деяких із них."
    ),
    "Select tag(s) in the list first.": "Спочатку виберіть теги у списку.",

    # ── bin_window.py ──────────────────────────────────────────────────────
    "Bin — Deleted Items": "Кошик — Видалені елементи",
    "Deleted at": "Дата видалення",
    "Restore Selected": "Відновити вибрані",
    "Delete Permanently": "Видалити назавжди",
    "Cleanup Old Items…": "Очистити старі елементи…",

    # ── backups.py ─────────────────────────────────────────────────────────
    "Restore an earlier version": "Відновити попередню версію",
    "Your database is backed up automatically after every change. Pick an earlier version below to restore it.": (
        "База даних резервується автоматично після кожної зміни. "
        "Виберіть попередню версію нижче для відновлення."
    ),
    "No saved versions yet. A backup is made automatically after every change.": (
        "Збережених версій ще немає. "
        "Резервна копія створюється автоматично після кожної зміни."
    ),
    "Restore this version": "Відновити цю версію",
    "Today": "Сьогодні",
    "Yesterday": "Вчора",
    "Most recent": "Найновіша",
    "Before your last restore": "Перед останнім відновленням",
    "today": "сьогодні",
    "yesterday": "вчора",
    "today {time}": "сьогодні {time}",
    "yesterday {time}": "вчора {time}",
    "the version from {date}": "версію від {date}",
    "the version from just before your last restore": "версію перед останнім відновленням",
    "Restore Version": "Відновити версію",
    "Restore {phrase}?\n\nYour current data is saved first, so you can undo this.": (
        "Відновити {phrase}?\n\nПоточні дані буде збережено спочатку, щоб ви могли скасувати це."
    ),
    "Your database has been restored to {phrase}.\n\nChanged your mind? Restore \"{before}\" to undo.": (
        "Вашу базу даних відновлено до {phrase}.\n\n"
        "Передумали? Відновіть «{before}», щоб скасувати."
    ),
    "Restore Error": "Помилка відновлення",
    "Sorry, that version could not be restored:\n{error}": "Вибачте, цю версію не вдалося відновити:\n{error}",
    "Remove Version": "Видалити версію",
    "Remove {phrase}?": "Видалити {phrase}?",
    "Remove Error": "Помилка видалення",
    "Sorry, that version could not be removed:\n{error}": "Вибачте, цю версію не вдалося видалити:\n{error}",

    # ── generate_text.py ───────────────────────────────────────────────────
    "Generate Text": "Генерувати текст",
    "Title…": "Заголовок…",
    "Generated text appears here…": "Тут з'явиться згенерований текст…",
    "Save to Texts": "Зберегти до текстів",
    "Save failed": "Помилка збереження",

    # ── audio_saver.py ─────────────────────────────────────────────────────
    "Save to Audio": "Зберегти як аудіо",
    "Generate one MP3 file from {count} word/translation pair(s).": (
        "Генерувати один MP3-файл із {count} пари(пар) слово/переклад."
    ),
    "Generating audio…": "Генерується аудіо…",
    "Compiling final audio file…": "Компілюється фінальний аудіофайл…",
    "Processed: {word}": "Оброблено: {word}",
    "Choose File && Start": "Вибрати файл та почати",
    "Cancelled.": "Скасовано.",
    "Audio saved": "Аудіо збережено",
    "Audio file saved to:\n{path}": "Аудіофайл збережено:\n{path}",
    "Audio Error": "Помилка аудіо",
    "Failed to save audio:\n{error}": "Не вдалося зберегти аудіо:\n{error}",
    "Cancelling…": "Скасовується…",

    # ── import_excel.py ────────────────────────────────────────────────────
    "Import from Excel": "Імпортувати з Excel",
    "Row": "Рядок",
    "Word 1": "Слово 1",
    "Language 1": "Мова 1",
    "Word 2": "Слово 2",
    "Language 2": "Мова 2",
    "Action": "Дія",
    "Details": "Деталі",
    "Add": "Додати",
    "Update": "Оновити",
    "Skip": "Пропустити",
    "All": "Всі",
    "To add": "До додавання",
    "To update": "До оновлення",
    "Skipped": "Пропущені",
    "Unrecognized": "Нерозпізнані",
    "Only recognized languages": "Лише розпізнані мови",
    "Exclude rows whose language wasn't recognized.":
        "Виключити рядки з нерозпізнаною мовою.",
    "Unrecognized language — will be imported exactly as written.":
        "Нерозпізнана мова — буде імпортовано точно як написано.",
    "Select all": "Вибрати всі",
    "Activity log": "Журнал дій",
    "Export log…": "Експортувати журнал…",

    # ── log_window.py ──────────────────────────────────────────────────────
    "Export…": "Експортувати…",

    # ── add_text.py ────────────────────────────────────────────────────────
    "Add Text": "Додати текст",
    "Write": "Написати",
    "AI Generate": "Генерувати AI",
    "Wikipedia": "Вікіпедія",
    "From URL": "З URL",
    "Language:": "Мова:",
    "Level:": "Рівень:",
    "Topic:": "Тема:",
    "Topic…": "Тема…",
    "Adapt to my level": "Адаптувати до мого рівня",
    "Load entries": "Завантажити записи",
    "Add feed…": "Додати стрічку…",
    "Ideas:": "Ідеї:",
    "Short (~100 words)": "Короткий (~100 слів)",
    "Medium (~250 words)": "Середній (~250 слів)",
    "Long (~500 words)": "Довгий (~500 слів)",
    "Travel": "Подорожі",
    "Food": "Їжа",
    "Daily routine": "Повсякденне",
    "A short story": "Коротке оповідання",
    "News": "Новини",
    "Dialogue at a café": "Діалог у кафе",
    "Type or paste your text here, or fetch one with the tabs above…": (
        "Введіть або вставте текст тут, або отримайте його за допомогою вкладок вище…"
    ),

    # ── texts_page.py ──────────────────────────────────────────────────────
    "Newest first": "Найновіші спочатку",
    "Oldest first": "Найстаріші спочатку",
    "Title A–Z": "Заголовок А–Я",
    "All languages": "Всі мови",
    "All levels": "Всі рівні",
    "All topics": "Всі теми",
    "No matching texts": "Немає відповідних текстів",
    "Try a different search or language filter.": "Спробуйте інший пошук або фільтр мови.",
    "New text (write or paste)": "Новий текст (написати або вставити)",
    "Get text from the Internet (AI / Wikipedia / URL / RSS)": (
        "Отримати текст з Інтернету (AI / Вікіпедія / URL / RSS)"
    ),
    "Import .txt file(s)": "Імпортувати .txt файл(и)",
    "Read aloud": "Читати вголос",
    "Translate text": "Перекласти текст",
    "Hide translation": "Сховати переклад",
    "Focus mode": "Режим фокусування",
    "Exit focus mode": "Вийти з режиму фокусування",
    "Paper mode: off": "Паперовий режим: вимкнено",
    "Paper: white (click for sepia)": "Паперовий: білий (натисніть для сепії)",
    "Paper: sepia (click to turn off)": "Паперовий: сепія (натисніть для вимкнення)",
    "Save Changes": "Зберегти зміни",
    "Previous text": "Попередній текст",
    "Next text": "Наступний текст",
    "From words: {words}": "Зі слів: {words}",
    "Created {date}": "Створено {date}",
    "Unsaved changes": "Незбережені зміни",
    "Save changes to '{title}'?": "Зберегти зміни до «{title}»?",
    "Changes saved.": "Зміни збережено.",
    "'{title}' moved to bin.": "«{title}» переміщено до кошика.",
    "Reader": "Читач",
    'Pronounce "{word}"': 'Вимовити «{word}»',
    'Add "{word}" to vocabulary': 'Додати «{word}» до словника',
    "Read from here": "Читати звідси",

    # ── word_model.py ──────────────────────────────────────────────────────
    "Source": "Джерело",
    "Added manually": "Додано вручну",
    "From reader": "З читанки",
    "Created at": "Дата створення",

    # ── word_popup.py ──────────────────────────────────────────────────────
    "Add with AI (lemma + best translation)": "Додати за допомогою AI (лема + найкращий переклад)",
    "Add to vocabulary as is": "Додати до словника як є",
    "Thinking…": "Думаю…",
    "'{pair}' is already in your dictionary.": "«{pair}» вже є у вашому словнику.",
    "{label} — {translation} · added": "{label} — {translation} · додано",

    # ── sync_popover.py ────────────────────────────────────────────────────
    "Cloud Sync": "Хмарна синхронізація",
    "Last sync": "Остання синхронізація",
    "Pending": "Очікує",
    "never": "ніколи",
    "just now": "щойно",
    "{n} min ago": "{n} хв тому",
    "Connected": "Підключено",
    "Not connected": "Не підключено",
    "change": "зміна",
    "changes": "зміни",
    "deletion": "видалення",
    "deletions": "видалень",
    "everything synced": "все синхронізовано",
    "Initial sync has not completed yet.": "Початкова синхронізація ще не завершена.",
    "Sync Now": "Синхронізувати",
    "Syncing…": "Синхронізується…",

    # ── player.py ──────────────────────────────────────────────────────────
    "Playback settings": "Налаштування відтворення",
    "Previous word": "Попереднє слово",
    "Next word": "Наступне слово",
    "Stop playback": "Зупинити відтворення",
    "Pause between words": "Пауза між словами",

    # ── reader.py ──────────────────────────────────────────────────────────
    "Nothing to read.": "Нічого читати.",
    "Previous sentence": "Попереднє речення",
    "Next sentence": "Наступне речення",
    "Reading speed": "Швидкість читання",
    "Sentence {n} / {total}": "Речення {n} / {total}",
    "buffering…": "буферизується…",

    # ── stats_page.py ──────────────────────────────────────────────────────
    "Overview": "Огляд",
    "Learning status": "Статус навчання",
    "Activity": "Активність",
    "Review activity": "Активність повторень",
    "Breakdown": "Деталі",
    "Total words": "Всього слів",
    "Mastered": "Засвоєно",
    "In progress": "В процесі",
    "Languages": "Мови",
    "Current streak": "Поточна серія",
    "Added this week": "Додано цього тижня",
    "Definitions written": "Написані визначення",
    "Status distribution": "Розподіл за статусом",
    "Words added over time": "Слова, додані з часом",
    "Activity calendar": "Календар активності",
    "Reviews over time": "Повторення з часом",
    "Review calendar": "Календар повторень",
    "Most reviewed words": "Найбільше повторювані слова",
    "Top language pairs": "Найпопулярніші мовні пари",
    "Top tags": "Найпопулярніші теги",
    "Reviewed this week": "Повторено цього тижня",
    "Total reviews": "Всього повторень",
    "Review streak": "Серія повторень",
    "{pct}% of all words": "{pct}% усіх слів",
    "actively learning": "активно навчаюся",
    "{n} pairs": "{n} пар(и)",
    "best {n}d": "рекорд {n} дн.",
    "{n} today": "{n} сьогодні",
    "listens logged": "прослуховувань записано",
    "keep it going": "продовжуйте!",
    "Day": "День",
    "Week": "Тиждень",
    "Month": "Місяць",

    # ── texts_page.py (additions) ──────────────────────────────────────────
    "Import text files": "Імпортувати текстові файли",
    "Text files (*.txt);;All files (*)": "Текстові файли (*.txt);;Всі файли (*)",
    "Language of the imported text(s):": "Мова імпортованого тексту(ів):",
    "Imported {count} text(s).": "Імпортовано {count} текст(ів).",
    "Some files could not be imported:": "Деякі файли не вдалося імпортувати:",
    "Import failed:\n{error}": "Помилка імпорту:\n{error}",
    "Failed to save text:\n{error}": "Не вдалося зберегти текст:\n{error}",
    "Failed to delete text:\n{error}": "Не вдалося видалити текст:\n{error}",
    "Delete Text": "Видалити текст",
    "Delete '{title}'?": "Видалити «{title}»?",
    "Unsupported language: {language}": "Непідтримувана мова: {language}",
    "Unsupported language: {lang}. Pick one from the list.":
        "Непідтримувана мова: {lang}. Виберіть одну зі списку.",
    "(empty)": "(порожньо)",
    "unsupported language": "непідтримувана мова",
    "unreadable text": "нечитабельний текст",
    "Skipped {n} {noun} ({reasons}).": "Пропущено {n} {noun} ({reasons}).",
    "Some text couldn't be read aloud — unsupported language "
    "or unreadable characters.":
        "Деякий текст не вдалося озвучити — непідтримувана мова "
        "або нечитабельні символи.",
    "Edit text": "Редагувати текст",
    "Done editing": "Завершити редагування",
    "Delete text": "Видалити текст",
    "Save Changes": "Зберегти зміни",
    "Paper mode": "Паперовий режим",
    'Click "+" to write or paste a text, the globe to fetch one\nfrom the Internet, or select words in the Words view and\nuse the "Text" action to generate a study text.': (
        "Натисніть «+», щоб написати або вставити текст, «глобус» — для завантаження\n"
        "з Інтернету, або виберіть слова у вікні «Слова»\n"
        "та скористайтесь дією «Текст» для генерації навчального тексту."
    ),

    # ── add_text.py (additions) ────────────────────────────────────────────
    "RSS": "RSS",
    'Searches Wikipedia in the selected language. Click a result to load the article; use "Adapt to my level" to simplify it.': (
        "Шукає Вікіпедію вибраною мовою. Натисніть на результат для завантаження статті; «Адаптувати до мого рівня» — для спрощення тексту."
    ),
    'News feeds for the selected language. Load a feed, then double-click an entry to fetch its full text. Add your own feeds with "Add feed…".': (
        "Стрічки новин вибраною мовою. Завантажте стрічку, потім двічі клацніть на записі для отримання повного тексту. Додайте власні стрічки за допомогою «Додати стрічку…»."
    ),
    "Length:": "Довжина:",
    "Search Wikipedia (in the selected language)…": "Пошук у Вікіпедії (вибраною мовою)…",
    "Double-click an entry to load its full text.": "Двічі клацніть на записі, щоб завантажити повний текст.",
    "Working…": "Обробляється…",
    "Show the {count} result(s) again": "Показати {count} результат(ів) знову",
    "{ai} API key is not set. Configure it in Settings → APIs → AI.": (
        "Ключ API {ai} не вказано. Налаштуйте його у Налаштуваннях → APIs → AI."
    ),
    "Generating with {ai}…": "Генерується з {ai}…",
    'Fetching "{title}"…': "Завантажується «{title}»…",
    "(yours)": "(ваша)",
    "Fetching the full text…": "Завантажується повний текст…",
    "Add feed": "Додати стрічку",
    "Feed name:": "Назва стрічки:",
    "Feed URL:": "URL стрічки:",
    "Failed to save the text.": "Не вдалося зберегти текст.",
    "Failed to save the text: {error}": "Не вдалося зберегти текст: {error}",
    "'{title}' saved.": "«{title}» збережено.",
    "(untitled)": "(без назви)",
    "Rewrite the text below for the selected CEFR level with {ai}": (
        "Переписати текст нижче для вибраного рівня CEFR за допомогою {ai}"
    ),

    # ── log_window.py (additions) ──────────────────────────────────────────
    "Export Log": "Експортувати журнал",

    # ── titlebar.py ────────────────────────────────────────────────────────
    "Minimize": "Згорнути",
    "Maximize": "Розгорнути",
    "Restore": "Відновити",

    # ── mini_player.py ─────────────────────────────────────────────────────
    "Show controls": "Показати керування",

    # ── widgets.py ─────────────────────────────────────────────────────────
    "No color": "Без кольору",
    "None": "Немає",
    "Choose Color": "Вибрати колір",

    # ── main_window.py (additions) ─────────────────────────────────────────
    "Cloud sync: idle": "Хмарна синхронізація: не активна",
    "Failed to open table:\n{error}": "Не вдалося відкрити таблицю:\n{error}",
    "Failed to save template:\n{error}": "Не вдалося зберегти шаблон:\n{error}",

    # ── settings_dialog.py (additions) ─────────────────────────────────────
    "Show / hide": "Показати / приховати",
    "Excel options": "Налаштування Excel",
    "CSV options": "Налаштування CSV",
    "Header lines are written at the top of the file — import tools like "
    "Anki read them (e.g. #separator:tab, #html:true). "
    "Column names themselves are not written.": (
        "Рядки заголовка записуються на початку файлу — програми імпорту на кшталт "
        "Anki їх зчитують (наприклад, #separator:tab, #html:true). "
        "Самі назви стовпців не записуються."
    ),
    "Copy a .ttf file into the app's fonts folder and use it": (
        "Скопіюйте файл .ttf до папки шрифтів програми й використовуйте його"
    ),
    "Used only when exporting words to an MP3 file. "
    "The voice itself is configured in the Audio tab.": (
        "Використовується лише під час експорту слів у файл MP3. "
        "Сам голос налаштовується на вкладці «Аудіо»."
    ),
    "The voice used everywhere words are spoken: in-app Read Aloud "
    "and MP3 export. gTTS is free and needs no setup. Google Cloud TTS "
    "needs a service-account JSON key (Cloud Console → IAM & Admin → "
    "Service Accounts → Keys) and billing enabled on the project — "
    "usage within the free monthly quota is not charged.": (
        "Голос, що використовується скрізь, де вимовляються слова: функція «Читати вголос» "
        "та експорт MP3. gTTS безкоштовний і не потребує налаштування. Google Cloud TTS "
        "потребує JSON-ключ службового облікового запису (Cloud Console → IAM & Admin → "
        "Service Accounts → Keys) і увімкненого білінгу — "
        "використання в межах безкоштовної місячної квоти не тарифікується."
    ),
    "Fully listening to a word in Read Aloud promotes it along the "
    "familiarity ladder New → Reviewing → Learning → Mastered. Each "
    "number is the total completed listens needed to reach that level — "
    "passive audio exposure is weak, so high values are normal. Words "
    "you set to Mastered or Ignored yourself are never changed, and a "
    "word is never demoted.": (
        "Повне прослуховування слова в «Читати вголос» просуває його по щаблях: "
        "Нове → Переглянуто → Вивчення → Засвоєно. Кожне число — це загальна кількість "
        "завершених прослуховувань для досягнення рівня. Слова, яким ви самі встановили "
        "статус «Засвоєно» або «Ігнороване», ніколи не змінюються, і слово ніколи "
        "не понижується в статусі."
    ),
    "Save a ready-made .xlsx with the right headers and example rows": (
        "Зберегти готовий .xlsx із правильними заголовками та прикладами рядків"
    ),
    "Google Translate (free)": "Google Translate (безкоштовно)",
    "Google Translate is free and needs no API key.": (
        "Google Translate безкоштовний і не потребує ключа API."
    ),
    "Usage": "Використання",
    "OpenAI (ChatGPT)": "OpenAI (ChatGPT)",
    "Google Gemini": "Google Gemini",
    "Click the field and press the desired key combination — it opens "
    "'Add Word' with the clipboard content from anywhere. "
    "Leave empty to disable.": (
        "Клацніть поле та натисніть потрібне поєднання клавіш — воно відкриє "
        "«Додати слово» зі вмістом буфера обміну звідусіль. "
        "Залиште порожнім, щоб вимкнути."
    ),
    "Add font…": "Додати шрифт…",
    "TrueType fonts (*.ttf)": "Шрифти TrueType (*.ttf)",
    "Could not copy the font file:\n{error}": "Не вдалося скопіювати файл шрифту:\n{error}",
    "Save import template…": "Зберегти шаблон імпорту…",
    "Excel files (*.xlsx)": "Файли Excel (*.xlsx)",
    "Template saved to:\n{path}\n\n"
    "Fill it with your words (replace the example rows) "
    "and import it via the app menu → Import Excel to Database.": (
        "Шаблон збережено в:\n{path}\n\n"
        "Заповніть його своїми словами (замініть приклади рядків) "
        "і імпортуйте через меню програми → Імпортувати Excel до бази даних."
    ),
    "Could not save the template:\n{error}": "Не вдалося зберегти шаблон:\n{error}",
    "Background image": "Фонове зображення",
    "Images (*.png *.jpg *.jpeg)": "Зображення (*.png *.jpg *.jpeg)",
    "JSON files (*.json)": "Файли JSON (*.json)",
    "Connection successful! ✅": "З'єднання успішне! ✅",
    "Could not connect. Check the URL/key and your internet connection.": (
        "Не вдалося підключитися. Перевірте URL, ключ та підключення до інтернету."
    ),
    "Connection test failed:\n{error}": "Тест з'єднання не вдався:\n{error}",
    "{count} / {limit} characters this period": "{count} / {limit} символів за цей період",
    "{count} characters used": "{count} символів використано",
    "Autostart": "Автозапуск",
    "Could not update autostart entry:\n{error}": "Не вдалося оновити запис автозапуску:\n{error}",
    "Google Cloud TTS": "Google Cloud TTS",
    "Google Cloud TTS is selected but {problem}\n\n"
    "Audio will fall back to gTTS until this is fixed.": (
        "Вибрано Google Cloud TTS, але {problem}\n\n"
        "Аудіо перейде на gTTS, поки це не буде виправлено."
    ),

    # ── Count nouns (for ntr() in backups.py / sync_popover.py) ───────────
    "word": "слово",
    "words": "слова",
    "text": "текст",
    "texts": "тексти",
    "tag": "тег",
    "tags": "теги",

    # ── Common (additions) ─────────────────────────────────────────────────
    "Translate": "Перекласти",
    "AI": "AI",
    "Save As": "Зберегти як",
    "Save Audio As": "Зберегти аудіо як",
    "Save PDF As": "Зберегти PDF як",
    "Added": "Додано",
    "Updated": "Оновлено",
    "Failed": "Не вдалось",
    "Checking…": "Перевіряється…",
    "Cleanup": "Очищення",
    "Permanent Delete": "Остаточне видалення",
    "No word": "Немає слова",
    "Category": "Категорія",
    "Bin": "Кошик",

    # ── main_window.py (additions 2) ───────────────────────────────────────
    "All tags": "Усі теги",
    "Filter by tag — {tag}": "Фільтр за тегом — {tag}",
    "(showing first {n})": "(показано перші {n})",
    "Texts: {total}": "Тексти: {total}",
    "Deleted with {n} error(s).": "Видалено з {n} помилкою(помилками).",
    "Failed to update: {error}": "Не вдалося оновити: {error}",
    "Failed to export:\n{error}": "Не вдалося експортувати:\n{error}",
    "Failed to export PDF:\n{error}": "Не вдалося експортувати PDF:\n{error}",
    "Failed to export TXT:\n{error}": "Не вдалося експортувати TXT:\n{error}",
    "PDF saved to {path}": "PDF збережено: {path}",
    "TXT file saved to {path}": "TXT-файл збережено: {path}",
    "Template saved to {path}": "Шаблон збережено: {path}",
    "{format} file saved to {path}": "Файл {format} збережено: {path}",
    "Using gTTS instead — {problem}\nFix it in Settings → Audio.": (
        "Використовується gTTS — {problem}\nВиправте це в Налаштуваннях → Аудіо."
    ),
    "Failed to load the database:": "Не вдалося завантажити базу даних:",
    "{selected} of {total} selected": "{selected} із {total} вибрано",

    # ── backups.py (additions) ─────────────────────────────────────────────
    "Saved {when} · {summary}": "Збережено {when} · {summary}",
    "the version from {date}": "версію від {date}",
    "Sorry, that version could not be restored:\n{error}": (
        "Вибачте, цю версію не вдалося відновити:\n{error}"
    ),
    "Sorry, that version could not be removed:\n{error}": (
        "Вибачте, цю версію не вдалося видалити:\n{error}"
    ),

    # ── bin_window.py (additions) ──────────────────────────────────────────
    "The Bin requires cloud sync (Supabase). Enable and configure it in Settings → APIs → Sync.": (
        "Кошик потребує хмарної синхронізації (Supabase). "
        "Увімкніть та налаштуйте її в Налаштуваннях → APIs → Синхронізація."
    ),
    "Restore {count} item(s)?": "Відновити {count} елемент(ів)?",
    "Restored {count} item(s).": "Відновлено {count} елемент(ів).",
    "Select item(s) to restore.": "Виберіть елемент(и) для відновлення.",
    "Permanently delete {count} item(s)?\n\nThis cannot be undone!": (
        "Остаточно видалити {count} елемент(ів)?\n\nЦю дію не можна скасувати!"
    ),
    "Permanently deleted {count} item(s).": "Остаточно видалено {count} елемент(ів).",
    "Select item(s) to delete permanently.": "Виберіть елемент(и) для остаточного видалення.",
    "No items older than {n} days found.": "Елементів, старших за {n} днів, не знайдено.",
    "Permanently delete {total} item(s) deleted more than {days} days ago?\n"
    "({words} words, {texts} texts)\n\nThis cannot be undone!": (
        "Остаточно видалити {total} елемент(ів), видалених більш ніж {days} днів тому?\n"
        "({words} слів, {texts} текстів)\n\nЦю дію не можна скасувати!"
    ),
    "Permanently deleted {count} old item(s).": "Остаточно видалено {count} старих елементів.",
    "Failed to load deleted items:\n{error}": "Не вдалося завантажити видалені елементи:\n{error}",
    "Failed to count old items:\n{error}": "Не вдалося підрахувати старі елементи:\n{error}",
    "Failed to cleanup:\n{error}": "Не вдалося очистити:\n{error}",

    # ── import_excel.py (additions) ────────────────────────────────────────
    "Import Excel": "Імпорт Excel",
    "Expected columns: Language1, Language2, Word1, Word2 — named in a header row, "
    "or headerless with the first four columns in that order. "
    "A ready-made template is available in the app menu → Save Import Template.": (
        "Очікувані стовпці: Language1, Language2, Word1, Word2 — іменовані у рядку заголовка "
        "або без заголовка з чотирма першими стовпцями у такому порядку. "
        "Готовий шаблон є в меню програми → Зберегти шаблон імпорту."
    ),
    "All ({n})": "Усі ({n})",
    "To add ({n})": "До додавання ({n})",
    "To update ({n})": "До оновлення ({n})",
    "Skipped ({n})": "Пропущені ({n})",
    "Unrecognized ({n})": "Нерозпізнані ({n})",
    " · {n} with unrecognized language": " · {n} з нерозпізнаною мовою",
    "{total} rows: {add} new · {update} updates · {skip} skipped": (
        "{total} рядків: {add} нових · {update} оновлень · {skip} пропущено"
    ),
    "Review the proposed changes, then import the selected rows.": (
        "Перегляньте запропоновані зміни, потім імпортуйте вибрані рядки."
    ),
    "Nothing to import — no new or changed entries found.": (
        "Немає чого імпортувати — нових або змінених записів не знайдено."
    ),
    "Analyzing file…": "Аналізується файл…",
    "Could not read the Excel file — see the activity log.": (
        "Не вдалося прочитати файл Excel — перегляньте журнал дій."
    ),
    "Analysis failed — see the activity log.": "Аналіз не вдався — перегляньте журнал дій.",
    "Import failed": "Помилка імпорту",
    "Import failed — see the activity log.": "Імпорт не вдався — перегляньте журнал дій.",
    "Importing…": "Імпортується…",
    "Importing {count} item(s)…": "Імпортується {count} елемент(ів)…",
    "Import {count} Item(s)": "Імпортувати {count} елемент(ів)",
    "Import finished:": "Імпорт завершено:",
    "Backup failed — see the activity log.": "Резервне копіювання не вдалось — перегляньте журнал дій.",
    "{n} added": "{n} додано",
    "{n} updated": "{n} оновлено",
    "{n} failed": "{n} не вдалось",
    "{n} failed.": "{n} не вдалось.",
    "Export Import Log": "Експортувати журнал імпорту",

    # ── definition.py (additions) ──────────────────────────────────────────
    "Definition — {word}": "Визначення — {word}",
    "Failed to save definition:\n{error}": "Не вдалося зберегти визначення:\n{error}",

    # ── edit_word.py (additions) ───────────────────────────────────────────
    "Edit — {word}": "Редагувати — {word}",

    # ── add_word.py (additions) ────────────────────────────────────────────
    "Failed to save word:\n{error}": "Не вдалося зберегти слово:\n{error}",

    # ── tags.py (additions) ────────────────────────────────────────────────
    "Attach the selected tag(s) to every selected word": (
        "Прикріпити вибрані теги до кожного вибраного слова"
    ),
    "Failed to add tag:\n{error}": "Не вдалося додати тег:\n{error}",
    "Failed to apply tags:\n{error}": "Не вдалося застосувати теги:\n{error}",
    "Failed to remove tags:\n{error}": "Не вдалося видалити теги:\n{error}",

    # ── generate_text.py (additions) ───────────────────────────────────────
    "Generates a text with AI using the Language, Level and Topic fields below. "
    "Pick a topic chip or type your own.": (
        "Генерує текст за допомогою AI, використовуючи поля Мова, Рівень і Тема нижче. "
        "Виберіть чіп теми або введіть власну."
    ),
    "Generating a {language} text from {count} word(s) with {ai}:": (
        "Генерується текст {language} із {count} слова(слів) за допомогою {ai}:"
    ),

    # ── add_text.py (additions) ────────────────────────────────────────────
    "Type or paste a text into the editor below, give it a title, "
    "set the language — then save.": (
        "Введіть або вставте текст у редактор нижче, дайте йому заголовок, "
        "встановіть мову — потім збережіть."
    ),
    "Extracts the readable article text from any web page. "
    "Pages behind logins or built purely with JavaScript may not work.": (
        "Витягує читабельний текст статті з будь-якої сторінки. "
        "Сторінки за логіном або побудовані лише на JavaScript можуть не працювати."
    ),

    # ── Strings missed by the initial pass ─────────────────────────────────
    # Toolbar action tooltips
    "View definition (double-click)": "Переглянути визначення (подвійний клік)",
    "Read selected words aloud": "Озвучити вибрані слова",
    "Toggle favorite": "Додати/прибрати з обраного",
    "Add / remove tags": "Додати / видалити теги",
    "Edit word": "Редагувати слово",
    "Copy words": "Копіювати слова",
    "Generate text from selection": "Згенерувати текст із вибраного",

    # File-dialog filters & titles
    "PDF files (*.pdf)": "Файли PDF (*.pdf)",
    "Excel files (*.xlsx *.xls)": "Файли Excel (*.xlsx *.xls)",
    "CSV files (*.csv)": "Файли CSV (*.csv)",
    "Text files (*.txt)": "Текстові файли (*.txt)",
    "MP3 files (*.mp3)": "Файли MP3 (*.mp3)",
    "Open Excel Table": "Відкрити таблицю Excel",
    "Save Import Template": "Зберегти шаблон імпорту",

    # Cloud sync status
    "Cloud sync": "Хмарна синхронізація",
    "Not connected. Check internet or credentials": "Немає підключення. Перевірте інтернет або облікові дані",
    "Syncing with cloud…": "Синхронізація з хмарою…",
    "Sync completed successfully": "Синхронізацію успішно завершено",
    "Sync enabled but not connected. Check settings.": "Синхронізацію ввімкнено, але немає підключення. Перевірте налаштування.",
    "idle": "не активна",
    "syncing": "синхронізація",
    "success": "успішно",
    "error": "помилка",

    # Chart empty states
    "No data yet": "Поки немає даних",
    "No activity yet": "Поки немає активності",
    "Not enough activity yet": "Поки замало активності",

    # Settings tabs
    "APIs": "API",
    "Audio (MP3)": "Аудіо (MP3)",
    "Sync": "Синхронізація",

    # Settings — AI/translation provider labels & notes
    "OpenAI API key (.env)": "Ключ OpenAI API (.env)",
    "Google API key (.env)": "Ключ Google API (.env)",
    'Billed per use — get a key at <a href="https://platform.openai.com/api-keys">platform.openai.com/api-keys</a>. Models: gpt-4o-mini, gpt-4o, gpt-4.1-mini… API usage — see <a href="https://platform.openai.com/usage">dashboard</a>.':
        'Оплата за використання — отримайте ключ на <a href="https://platform.openai.com/api-keys">platform.openai.com/api-keys</a>. Моделі: gpt-4o-mini, gpt-4o, gpt-4.1-mini… Використання API — див. <a href="https://platform.openai.com/usage">панель</a>.',
    'Free tier available — get a key at <a href="https://aistudio.google.com/app/apikey">aistudio.google.com/app/apikey</a>. Models: gemini-2.5-flash, gemini-2.5-flash-lite… API usage — see <a href="https://aistudio.google.com/usage">AI Studio</a>.':
        'Доступний безкоштовний тариф — отримайте ключ на <a href="https://aistudio.google.com/app/apikey">aistudio.google.com/app/apikey</a>. Моделі: gemini-2.5-flash, gemini-2.5-flash-lite… Використання API — див. <a href="https://aistudio.google.com/usage">AI Studio</a>.',
    'Get a key at <a href="https://www.deepl.com/pro-api">deepl.com/pro-api</a>. Use https://api-free.deepl.com/v2/translate for free-tier keys.':
        'Отримайте ключ на <a href="https://www.deepl.com/pro-api">deepl.com/pro-api</a>. Для безкоштовних ключів використовуйте https://api-free.deepl.com/v2/translate.',

    # Excel import help (settings)
    "<ol style='margin:0'><li>Prepare an Excel file with the columns <b>Language1, Language2, Word1, Word2</b> — named like that in a header row (extra columns are ignored), or without headers, with the first four columns in exactly that order.</li><li>Open the app menu → <i>Import Excel to Database…</i> and choose the file.</li><li>Review the proposed rows and click <i>Import</i>.</li></ol>":
        "<ol style='margin:0'><li>Підготуйте файл Excel зі стовпцями <b>Language1, Language2, Word1, Word2</b> — саме з такими назвами в рядку заголовків (зайві стовпці ігноруються), або без заголовків, де перші чотири стовпці йдуть саме в цьому порядку.</li><li>Відкрийте меню застосунку → <i>Імпортувати Excel до бази даних…</i> і виберіть файл.</li><li>Перегляньте запропоновані рядки та натисніть <i>Імпорт</i>.</li></ol>",

    # About dialog
    "created by": "створив",
    "Version": "Версія",
    "Build": "Збірка",
    "Your personal vocabulary companion with cloud sync, AI definitions, translations, text-to-speech and export options.":
        "Ваш персональний помічник для вивчення слів із хмарною синхронізацією, визначеннями від ШІ, перекладами, озвучуванням та експортом.",
    "Licensed under the GNU Affero General Public License v3.0. This attribution must be preserved (AGPL §7).":
        "Ліцензовано згідно з GNU Affero General Public License v3.0. Це зазначення авторства має бути збережено (AGPL §7).",
    "Found a bug or have an idea?": "Знайшли помилку або маєте ідею?",
    "Report an issue": "Повідомити про проблему",

    # Updates
    "Updates": "Оновлення",
    "Check for updates": "Перевірити оновлення",
    "You're up to date.": "У вас встановлено останню версію.",
    "Update available": "Доступне оновлення",
    "Lingueez {version} is available — you have {current}.":
        "Доступна версія Lingueez {version} — у вас {current}.",
    "Skip this version": "Пропустити цю версію",
    "Later": "Пізніше",
    "Download": "Завантажити",
    "Check for updates on startup": "Перевіряти оновлення під час запуску",
    "Checks once a day for a newer version and lets you know; "
    "nothing is ever downloaded or installed automatically.":
        "Раз на день перевіряє наявність новішої версії та сповіщає вас; "
        "нічого не завантажується й не встановлюється автоматично.",

    # Misc units
    "in": "дюйм",
    " s": " с",

    # Word statuses (stored in English; only the displayed label is localized)
    "New": "Нове",
    "To Learn": "Вивчити",
    "Reviewing": "Переглянуто",
    "Ignored": "Ігнороване",
    # "Learning" and "Mastered" are translated above.

    # Table density (settings → Table size)
    "Compact": "Компактний",
    "Normal": "Звичайний",
    "Comfortable": "Комфортний",
    "Spacious": "Просторий",

    # Language names (stored in English as the canonical DeepL/gTTS key;
    # only the displayed label is localized — see app/i18n.py lang_label).
    "English": "Англійська",
    "German": "Німецька",
    "Spanish": "Іспанська",
    "Ukrainian": "Українська",
    "French": "Французька",
    "Italian": "Італійська",
    "Portuguese": "Португальська",
    "Russian": "Російська",
    "Greek": "Грецька",
    "Arabic": "Арабська",
    "Bengali": "Бенгальська",
    "Cantonese": "Кантонська",
    "Hindi": "Гінді",
    "Japanese": "Японська",
    "Korean": "Корейська",
    "Mandarin": "Мандаринська",
    "Polish": "Польська",
    "Turkish": "Турецька",
    "Vietnamese": "В’єтнамська",
    "Afrikaans": "Африкаанс",
    "Albanian": "Албанська",
    "Amharic": "Амхарська",
    "Armenian": "Вірменська",
    "Azerbaijani": "Азербайджанська",
    "Basque": "Баскська",
    "Belarusian": "Білоруська",
    "Bosnian": "Боснійська",
    "Bulgarian": "Болгарська",
    "Catalan": "Каталонська",
    "Cebuano": "Себуанська",
    "Chichewa": "Чичева",
    "Chinese": "Китайська",
    "Croatian": "Хорватська",
    "Czech": "Чеська",
    "Danish": "Данська",
    "Dutch": "Нідерландська",
    "Estonian": "Естонська",
    "Filipino": "Філіппінська",
    "Finnish": "Фінська",
    "Galician": "Галісійська",
    "Georgian": "Грузинська",
    "Gujarati": "Гуджараті",
    "Haitian Creole": "Гаїтянська креольська",
    "Hausa": "Хауса",
    "Hawaiian": "Гавайська",
    "Hebrew": "Іврит",
    "Hmong": "Хмонг",
    "Hungarian": "Угорська",
    "Icelandic": "Ісландська",
    "Igbo": "Ігбо",
    "Indonesian": "Індонезійська",
    "Irish": "Ірландська",
    "Javanese": "Яванська",
    "Kannada": "Каннада",
    "Kazakh": "Казахська",
    "Khmer": "Кхмерська",
    "Kinyarwanda": "Кіньяруанда",
    "Kyrgyz": "Киргизька",
    "Lao": "Лаоська",
    "Latin": "Латина",
    "Latvian": "Латиська",
    "Lithuanian": "Литовська",
    "Luxembourgish": "Люксембурзька",
    "Macedonian": "Македонська",
    "Malagasy": "Малагасійська",
    "Malay": "Малайська",
    "Malayalam": "Малаялам",
    "Maltese": "Мальтійська",
    "Maori": "Маорі",
    "Marathi": "Маратхі",
    "Mongolian": "Монгольська",
    "Myanmar (Burmese)": "М’янмська (бірманська)",
    "Nepali": "Непальська",
    "Norwegian": "Норвезька",
    "Odia": "Орія",
    "Pashto": "Пушту",
    "Persian": "Перська",
    "Punjabi": "Панджабі",
    "Romanian": "Румунська",
    "Samoan": "Самоанська",
    "Scots Gaelic": "Шотландська гельська",
    "Serbian": "Сербська",
    "Sesotho": "Сесото",
    "Shona": "Шона",
    "Sindhi": "Сіндхі",
    "Sinhala": "Сингальська",
    "Slovak": "Словацька",
    "Slovenian": "Словенська",
    "Somali": "Сомалійська",
    "Sundanese": "Сунданська",
    "Swahili": "Суахілі",
    "Swedish": "Шведська",
    "Tajik": "Таджицька",
    "Tamil": "Тамільська",
    "Tatar": "Татарська",
    "Telugu": "Телугу",
    "Thai": "Тайська",
    "Turkmen": "Туркменська",
    "Urdu": "Урду",
    "Uyghur": "Уйгурська",
    "Uzbek": "Узбецька",
    "Welsh": "Валлійська",
    "Xhosa": "Кхоса",
    "Yiddish": "Їдиш",
    "Yoruba": "Йоруба",
    "Zulu": "Зулуська",
}

# Date names, read by app.i18n. Months are in the genitive case because they
# only appear in formatted dates ("13 червня 2026"). Weekdays start on Monday
# (datetime.weekday(): 0 = Monday).
MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
          "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
MONTHS_ABBR = ["січ", "лют", "бер", "кві", "тра", "чер",
               "лип", "сер", "вер", "жов", "лис", "гру"]
WEEKDAYS = ["Понеділок", "Вівторок", "Середа", "Четвер",
            "П'ятниця", "Субота", "Неділя"]
WEEKDAYS_ABBR = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
