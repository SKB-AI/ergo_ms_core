# Возможности модулей

Этот документ — каталог того, **чем модуль может расширять ERGO MS** без правок ядра под конкретное имя модуля. Он рассчитан на разработчика модуля: что положить в `modules/<имя>/`, как ядро это находит и куда смотреть за деталями.

Общая картина архитектуры — в [architecture.md](architecture.md). Эталон каркаса — учебный модуль `modules/module_template/` (README и HOWTO внутри него).

## Принцип расширения

Ядро **не импортирует** доменный код модулей и **не содержит** литералов их имён в runtime. Модуль подключается одним из двух способов:

1. **Hook discovery** — файл с фиксированным именем (`apps.py`, `routes.js`, `host_lifecycle.yaml` и т.п.). Ядро сканирует каталог включённых модулей и агрегирует объявления.
2. **ModuleBridge** — операции, группы и события с именами-контрактами. Модуль регистрирует провайдер в `integrations.py` / `client/js/integrations.js`; другой модуль или ядро вызывают `bridge.call` / `bridge.all` без прямого import.

Отключённый модуль (`DISABLED_MODULES` в `.env`) не участвует в discovery, зависимостях, меню и Docker build context — см. [configuration.md](configuration.md#настройки-модулей).

Проверка изоляции и контрактов: `ergoms core-rules-check` (в том числе наличие `README.md` и `AGENTS.md` у установленного модуля). Вынос в отдельный процесс и схему — [modularization.md](modularization.md). Правила для агента Cursor: [`.cursor/rules/modules.mdc`](../.cursor/rules/modules.mdc), [`.cursor/rules/core-module-isolation.mdc`](../.cursor/rules/core-module-isolation.mdc), [`.cursor/rules/module-contracts.mdc`](../.cursor/rules/module-contracts.mdc), [`.cursor/rules/modularization.mdc`](../.cursor/rules/modularization.mdc). Документация самого модуля (`AGENTS.md`, `.cursor/rules/`, `README.md`) — [`.cursor/rules/module-docs.mdc`](../.cursor/rules/module-docs.mdc): обновляйте её вместе с контрактами и безопасностью.

## Карта каталога модуля

Не все файлы обязательны. Ниже — что можно объявить и зачем.

```
modules/<имя>/
├── api/
│   ├── apps.py                 # регистрация Django-приложения
│   ├── urls.py, views.py, …    # REST API
│   ├── integrations.py         # ModuleBridge (сервер)
│   ├── permission_catalog.py   # права ADP
│   ├── celery_config.py        # очереди Celery
│   ├── celery_beat_config.py   # периодические задачи
│   ├── notification_catalog.py # события уведомлений (через мост)
│   ├── bridge_manifest.yaml    # ops/groups при MODULE_RUNTIME=microservice
│   ├── schema.yaml             # схема PostgreSQL m_<name>, isolated
│   ├── search_indexes.py       # индексы поиска (uid с префиксом модуля)
│   ├── user_guides/            # шпаргалки пакета knowledge/ (необязательно)
│   └── migrations/             # схема БД + пункты бокового меню
├── client/
│   ├── js/
│   │   ├── routes.js           # страницы Vue Router
│   │   ├── endpoints.js        # адреса API для клиента
│   │   ├── permission-rules.js # UX-права маршрутов
│   │   ├── permission-sections.js
│   │   ├── routeGuard.js       # guard маршрутов
│   │   ├── integrations.js     # ModuleBridge (клиент)
│   │   ├── locales.js          # i18n ru/en/…
│   │   ├── theme-defaults.js   # отдельная палитра модуля
│   │   └── clientEnv.js        # чтение CLIENT_<МОДУЛЬ>_*
│   ├── styles/theme-bootstrap.scss
│   └── LayoutPlugin.vue        # layout / offcanvas (альтернатива группе моста)
├── mcp/                        # MCP-сервер для Cursor
├── .cursor/rules/              # правила Cursor модуля
├── AGENTS.md                   # обязательный указатель для агента
├── README.md                   # обязательный обзор для человека
├── ergoms.conf                 # команды ergoms <имя>:<команда>
├── ergoms.help.yaml            # справка help module
├── locales/<lang>/ergoms.help.yaml
├── packages.yaml               # portable-бинарники
├── huggingface_models.yaml     # снимки Hugging Face
├── host_lifecycle.yaml         # install/uninstall/stop OS-службы
├── process_roles.yaml          # роли в ergoms resource-usage
├── vscode.tasks.yaml           # Run Task + setup-full / start-all
├── integrations.yaml           # requires / extends между модулями
├── pyproject.toml              # Python-зависимости модуля
└── client/package.json         # npm-зависимости клиента модуля
```

## Быстрый выбор: что добавить

| Нужно | Куда |
|-------|------|
| REST API и модели | `api/` + `apps.py`, `urls.py` |
| Страницы в оболочке | `client/js/routes.js` + миграция меню |
| Права модуля в ADP | `api/permission_catalog.py` |
| Фоновые задачи | `api/tasks.py` + `celery_config.py` |
| Вызов из другого модуля / ядра | ModuleBridge в `integrations.py` |
| Пакет справки | `api/user_guides/*.md` и `user_description` в каталоге прав; процесс пишет `knowledge/<имя>/`, чтение — `load_published_pack_documents` |
| Своя схема PostgreSQL / запрет FK наружу | `api/schema.yaml` + [modularization.md](modularization.md) |
| Обязательная / расширяющая зависимость модуля | `integrations.yaml` (`requires` / `extends`) |
| Свои команды CLI | `ergoms.conf` + `ergoms.help.yaml` |
| Участие в Setup Full / Start All | `vscode.tasks.yaml` → `include_in` |
| OS-служба вместе с install-services | `host_lifecycle.yaml` (install **и** uninstall) |
| Portable-бинарник (ollama, ffmpeg…) | `packages.yaml` |
| Снимок Hugging Face (`org/name`) | `huggingface_models.yaml` |
| Учёт процесса в resource-usage | `process_roles.yaml` |
| Отдельная тема оформления | `theme-defaults.js` + `theme-bootstrap.scss` |
| Инструменты агента Cursor | `mcp/` + `.cursor/rules/` |

---

## Сервер (Django / API)

### Регистрация приложения

Файл `api/apps.py` обязателен для модулей с серверной частью. `ModuleDiscoverer` находит его сам; вручную в `INSTALLED_APPS` модуль не добавляют. Зависимости от других модулей — корневой `integrations.yaml`: `requires` (обязательные, порядок загрузки; нет зависимости — потребитель пропускается, остальные модули стартуют) и `extends` (опциональные расширяющие; отсутствие не мешает старту). Имя из `BRIDGE_SERVICE_URLS` или `MICROSERVICE_MODULES` закрывает `requires`, даже если папки модуля нет на этом хосте: сосед доступен через HTTP-мост. Не заносите только удалённый модуль в `MICROSERVICE_MODULES` — это список локальных процессов. Разнесение по серверам — [modularization.md](modularization.md#несколько-серверов-без-выноса-всего-хоста).

При командах схемы БД (`migrate` / `makemigrations`) у модулей пропускается `ready()` и не подключаются URL — схема строится без runtime-моста.

### URL и views

`api/urls.py` подхватывается discovery. В URL снаружи наружу отдавайте **`public_id` (UUID)**, не числовой pk — см. правила безопасности проекта.

### Права ADP

`api/permission_catalog.py` — декларации прав модуля. Ядро ADP агрегирует каталоги всех включённых модулей. Клиентские `permission-rules.js` — только UX (скрытие пунктов); решение о доступе принимает API.

### Celery

- Задачи: `api/tasks.py`
- Очереди и маршруты: `api/celery_config.py` (префикс маршрута вида `modules.<имя>.api.tasks.*`)
- Периодические задачи: `api/celery_beat_config.py`

Запуск: `ergoms start-worker`, `ergoms start-beat`. Подробности — [`.cursor/rules/celery.mdc`](../.cursor/rules/celery.mdc).

### Боковое меню

Пункты sidebar живут в БД (`MenuItem`). Модуль добавляет их **миграцией** через `MenuMigrationHelper`, а не JSON на клиенте. Маршруты страниц — в `client/js/routes.js`. Порядок и видимость администратор меняет в CMS.

Эталон: `modules/module_template/api/migrations/` с `*_add_menu.py`. Правило: [`.cursor/rules/menu.mdc`](../.cursor/rules/menu.mdc).

### Уведомления и аудит

Модули **не** импортируют внутренности подсистем уведомлений и аудита. Запись и каталоги — через ModuleBridge:

| Задача | Как |
|--------|-----|
| Каталог событий уведомлений | `notification_catalog.py` + `bridge.provide_many('notifications.event_definitions', …)` |
| Создать уведомление | `bridge.call('notifications.create', …)` |
| Каталог действий аудита | группа `audit.action_definitions` |
| CRUD-автоаудит | миксин `AuditedModelMixin` на ViewSet |

Правила: [`.cursor/rules/notifications.mdc`](../.cursor/rules/notifications.mdc), [`.cursor/rules/audit.mdc`](../.cursor/rules/audit.mdc).

### Session-claims при логине

Ядро при логине запрашивает opaque dict claims: `bridge.call(SESSION_RESTORE_CLAIMS, user=user)`. Ключи claims ядру неизвестны — их объявляет модуль-владелец scope через platform-контракты (`session_context.claims`, `session.restore_claims`). Подробности — [`.cursor/rules/module-contracts.mdc`](../.cursor/rules/module-contracts.mdc).

### Служебные письма учётки

Тема и тело писем собирает ядро. Модуль может подменить их операциями `core.compose_registration_invitation`, `core.compose_import_welcome_defaults`, `core.compose_password_reset_code`, `core.compose_admin_password_reset` (`{subject, body, html_body?}`). Если никто их не регистрирует, провайдер вернул пустой ответ или упал, уходит текст ядра. Получателя и адрес отправителя модуль не задаёт. Подробности — [`.cursor/rules/module-contracts.mdc`](../.cursor/rules/module-contracts.mdc).

### Microservice runtime

При `MODULE_RUNTIME=microservice` модуль может описать владельца ops/groups моста в `api/bridge_manifest.yaml` (для HTTP-маршрутизации между сервисами). Обычный монолитный host-режим этот файл не требует.

---

## Клиент (Vue)

Файлы из `client/js/` подхватывает `ModuleLoader` (prebuild glob). Регистрировать модуль в настройках клиента вручную не нужно. Папка — `client/js/`, не `client/src/js/`.

| Hook | Назначение |
|------|------------|
| `routes.js` | Маршруты Vue Router |
| `endpoints.js` | Адреса API для клиента |
| `permission-rules.js` | UX-правила доступа к маршрутам |
| `permission-sections.js` | Секции прав в UI |
| `routeGuard.js` | Доменный guard (после platform session-scope) |
| `integrations.js` | Регистрация client-контрактов моста |
| `locales.js` | Каталоги i18n модуля |
| `theme-defaults.js` | Дефолты палитры модуля (пара light+dark) |
| `LayoutPlugin.vue` | Плагин layout / offcanvas |

Вложенные `client/<раздел>/js/routes.js` и `integrations.js` тоже подхватываются. `endpoints.js` и `permission-rules.js` — из корневого `client/js/` (или re-export).

### Оболочка через мост (клиент)

Регистрация в `client/js/integrations.js` (ленивые import внутри обработчиков):

| Группа моста | Что даёт |
|--------------|----------|
| `layout.plugin_registry` | LayoutPlugin / offcanvas |
| `header.userMenu.items` | Пункты меню пользователя в шапке |
| `apps.menu.items` | Пункты AppsMenu в toolbar (кнопка скрыта без пунктов; пункты без фильтра по праву модуля) |
| `shell.floating_widgets` | Плавающие виджеты (`id`, `component`, `order`, `isVisible`) |
| `session_scope.module_context` | Контекст session-scoped модуля |
| `session.scope_entry_routes` | Welcome / home / onboarding |
| `session.scope_gating_claim` | JWT claim активного scope |
| `menu.removed_route_names` | Скрыть route names в меню |
| `menu.scope_required_route_prefixes` | Префиксы, требующие активный scope |

Это **не** боковое меню: sidebar — миграции API; шапка и AppsMenu — ModuleBridge.

### Темы

Отдельная настраиваемая палитра модуля — только через `theme-defaults.js` + `theme-bootstrap.scss` и редактор ядра `/settings/themes`. Без своей палитры достаточно CSS-переменных сайта (`var(--ui-*)`). Правило: [`.cursor/rules/themes.mdc`](../.cursor/rules/themes.mdc).

### Локализация и env клиента

- Строки UI — `client/js/locales.js` (namespace = имя модуля).
- Настройки модуля для бандла — `CLIENT_<МОДУЛЬ>_*` в корневом или модульном `.env`; в коде — локальный `client/js/clientEnv.js`, не `VITE_*`.

Выпадающие списки в UI — компонент ядра `SelectBox`, не нативный `<select>`.

---

## ModuleBridge

Единственный способ связи **модуль ↔ модуль** и участия модуля в сценариях ядра (логин, меню, audit, уведомления).

**Сервер** — `api/integrations.py`:

```python
from src.core.integrations import bridge

@bridge.provide_op('<name>.get_user_entity_ids')
def _handler(*, user=None, **_):
    ...
```

**Клиент** — `client/js/integrations.js` + `@/integrations/ModuleBridge.js`.

Имена platform-контрактов, которые **ядро само** вызывает, лежат в каталоге ядра (`module_contracts.py` / `moduleContracts.js`). Связи только между модулями (ядро группу не читает) в каталог ядра **не** кладут — константа у хоста, у провайдера та же строка локально.

Полный список platform-контрактов и матрица hook-файлов discovery — [`.cursor/rules/module-contracts.mdc`](../.cursor/rules/module-contracts.mdc).

---

## Развёртывание и ergoms

Эти hook’и работают **вне Django**: YAML + каталог включённых модулей.

### Команды модуля

| Файл | Назначение |
|------|------------|
| `ergoms.conf` | Команды `ergoms <имя>:<команда>` |
| `ergoms.help.yaml` | Русский source справки (`ergoms help module <имя>`) |
| `locales/<lang>/ergoms.help.yaml` | Переводы справки |

Справочник CLI: [cli.md](cli.md#команды-модулей). Правило: [`.cursor/rules/ergoms-module-help.mdc`](../.cursor/rules/ergoms-module-help.mdc).

### Setup Full и Start All (`vscode.tasks.yaml`)

Модуль объявляет задачи IDE и участие в агрегатах:

```yaml
module: <name>
tasks:
  - label: "My Module: Install"
    detail: "Установить зависимости модуля"
    command: "ergoms <name>:install"
    hide: true
    include_in:
      - setup-full
  - label: "My Module: Start"
    detail: "Запустить демон модуля"
    command: "ergoms <name>:start-daemon"
    panel: new
    include_in:
      - start-all
```

| `include_in` | Куда попадает |
|--------------|---------------|
| `setup-full` | шаг модульных задач в `ergoms setup` / Setup Full System (**до** миграций) |
| `setup-full-after-migrate` | тот же рецепт **после** миграций (нужна схема БД: RAG sync и т.п.) |
| `start-all` | `ergoms start-all` и multi-terminal Module Services |

Корневой `.vscode/tasks.json` для модульных команд **не** правят. Правило: [`.cursor/rules/module-vscode-tasks.mdc`](../.cursor/rules/module-vscode-tasks.mdc).

### OS-службы (`host_lifecycle.yaml`)

Парные команды для `ergoms install-services` и `ergoms uninstall-services` (и тестов deployment):

```yaml
module: <name>
host:
  stop_commands:
    - <name>:stop-daemon
  install_service_commands:
    - <name>:install-daemon-service
  uninstall_service_commands:
    - <name>:uninstall-daemon-service
  service_units:
    - ergo-my-daemon
```

| Поле | Когда вызывается |
|------|------------------|
| `install_service_commands` | `ergoms install-services`. Команды `install-module-service` (API/worker/beat модуля) — только при `MODULE_RUNTIME=microservice` и имени в `MICROSERVICE_MODULES`; демоны без split процесса ставятся всегда |
| `uninstall_service_commands` | `ergoms uninstall-services` |
| `stop_commands` | остановка демонов в тестах deployment |
| `service_units` | имена OS-служб для `ergoms start` / `stop` / `restart` / `status`; start/stop/restart и uninstall молча пропускают не установленные. API/worker/beat модуля в start/stop только в microservice-режиме |

Снимается **служба**, не portable-пакет. Подробности: [deployment.md](deployment.md), [lifecycle-pipeline.md](lifecycle-pipeline.md), [`.cursor/rules/host-lifecycle.mdc`](../.cursor/rules/host-lifecycle.mdc).

### Portable-пакеты (`packages.yaml`)

Бинарники в `virtual_env/packages/` объявляют в `packages.yaml`; установка — `ergoms package-install <пакет>` (часто алиас в `ergoms.conf`). Download в Django management и хардкод имён модулей в ядре запрещены. Правило: [`.cursor/rules/packages.mdc`](../.cursor/rules/packages.mdc).

### Снимки Hugging Face (`huggingface_models.yaml`)

Веса `org/name` объявляют в `huggingface_models.yaml`; установка — `core/deployment/huggingface` и `ergoms pull-huggingface-models` (шаг setup-full). Рабочая копия — `virtual_env/trained_models/huggingface/<org>/<name>`. Кэш Hub в `virtual_env/cache/huggingface/` — только черновик качалки. Runtime — `resolve_huggingface_source` / `ensure_local_source`. Это не `ollama pull` и не `packages.yaml`. Правило: [`.cursor/rules/huggingface-models.mdc`](../.cursor/rules/huggingface-models.mdc).

### Роли процессов (`process_roles.yaml`)

Чтобы `ergoms resource-usage` показывал демон модуля, объявите роль в `process_roles.yaml` (имена процессов и маркеры cmdline/cwd). Классификатор ядра под модуль не правят. Правило: [`.cursor/rules/process-roles.mdc`](../.cursor/rules/process-roles.mdc).

### Зависимости Python и npm

| Слой | Где | Команды |
|------|-----|---------|
| Python модуля | `modules/<имя>/pyproject.toml` | `ergoms api module-add` / `module-remove`, `ergoms python-install` |
| npm клиента модуля | `modules/<имя>/client/package.json` | `ergoms npm run install:all` |

Lock ядра не должен впитывать модульные пакеты — см. [cli.md](cli.md#lock-файлы-ядро-и-модули).

---

## Инструменты разработчика (Cursor)

### Правила модуля

Доменные инструкции агенту — в `modules/<имя>/.cursor/rules/*.mdc` (globs от корня workspace: `modules/<имя>/**`) и кратко в `AGENTS.md`. Подхват: расширение **ERGO MS Module Cursor Rules** (`ergoms install-extensions`, команда Sync Module Cursor Rules). Платформенные правила остаются в корневом `.cursor/rules/`.

### MCP-сервер модуля

Код — только в `modules/<имя>/mcp/` (`manifest.yaml` + `server.py`). В Cursor — отдельная строка сервера на модуль. Подхват: расширение **ERGO MS Module Cursor MCP**. Правило: [`.cursor/rules/mcp.mdc`](../.cursor/rules/mcp.mdc).

---

## Docker и nginx

При микросервисном режиме модули могут участвовать в generate compose / upstream nginx как отдельные сервисы (`MODULE_RUNTIME=microservice`, списки модулей в env). Host-режим со службами ОС описан выше (`host_lifecycle`, `install-services`). См. [docker.md](docker.md) и [`.cursor/rules/docker.mdc`](../.cursor/rules/docker.mdc).

---

## Чего делать нельзя

- Прямые import между модулями (`from modules.<другой>…` / import компонентов другого модуля на клиенте).
- Правки ядра ради одного модуля (хардкод имени, палитр, команд stop/install).
- JWT и контекст сессии в `localStorage` вне `tokenStorage.js`.
- Числовые id из БД в URL, query и browser storage — только `public_id`.
- Свой редактор темы модуля в обход hook `theme-defaults.js`.
- Команды установки и миграций мимо `ergoms`.

---

## См. также

| Тема | Документ / правило |
|------|-------------------|
| Архитектура ядра и модулей | [architecture.md](architecture.md) |
| Структура каталогов | [structure.md](structure.md) |
| Команды ergoms | [cli.md](cli.md) |
| Службы ОС | [deployment.md](deployment.md) |
| Lifecycle-pipeline | [lifecycle-pipeline.md](lifecycle-pipeline.md) |
| Структура модуля (агент) | [`.cursor/rules/modules.mdc`](../.cursor/rules/modules.mdc) |
| Platform-контракты и hook-матрица | [`.cursor/rules/module-contracts.mdc`](../.cursor/rules/module-contracts.mdc) |
| Изоляция ядра | [`.cursor/rules/core-module-isolation.mdc`](../.cursor/rules/core-module-isolation.mdc) |
| Учебный модуль | `modules/module_template/` |
