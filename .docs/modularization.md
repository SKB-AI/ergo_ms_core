# Вынос модулей в отдельные сервисы

По умолчанию ERGO MS работает как **монолит**: один процесс Django, одна база `default`, мост `BRIDGE_TRANSPORT=local`. Режим разработки так и остаётся. Этот документ описывает, как поэтапно вынести модуль в отдельный процесс, затем в свою схему PostgreSQL и при необходимости в отдельную базу — без замены ModuleBridge и без Kubernetes.

Каркас процесса уже есть (`ergoms start-module`, HTTP-мост, nginx `/api/<name>/`). Схемы и отдельная БД подключаются хуками модуля, а не хардкодом имён в ядре.

Правило для агента Cursor: [`.cursor/rules/modularization.mdc`](../.cursor/rules/modularization.mdc). Каталог hook’ов модуля — [modules.md](modules.md). Команды — [cli.md](cli.md#профили-запуска-монолит-и-microservice).

## Уровни

| Уровень | Процессы | Данные | Когда включать |
|---------|----------|--------|----------------|
| **0** | Один API, worker грузит все модули | PostgreSQL: ядро в `core`, модули в `m_<name>`; `public` нет. SQLite — один файл | Разработка (`MODULE_RUNTIME=monolith`) |
| **1** | Ядро + API модуля за nginx, worker только своей очереди | Та же БД | Пилот за прокси, деградация UI при падении модуля |
| **2** | Как 1 или ещё общий процесс | Схема `m_<name>`, ядро в `core` | Изоляция данных без отдельного инстанса PostgreSQL |
| **3** | Как 1 | Своя БД (`databases.yaml` + `module:`) | Свой цикл релизов, отдельный SLO бэкапа, реальная нагрузка |

Рабочий порядок: **1 → 2 → 3**. Схема без split процессов допустима как промежуточный шаг уровня 2. Отдельная база без исходящей очереди событий (outbox) — нет.

## Архитектурные решения

**Смешанный подход.** По умолчанию — одна PostgreSQL и схема на модуль. Отдельная база — исключение, не норма для каждого модуля.

**Общее ядро остаётся в ядре.** Пользователи (`auth_user.public_id`), сессии устройств, роли ADP, меню CMS, аудит, inbox уведомлений принадлежат ядру. Модуль не владеет пользователем: хранит `user_public_id` (UUID), без внешнего ключа на чужие таблицы. Пункты меню по-прежнему пишутся в таблицы ядра через миграции / `restore_menu`.

**Мост не меняем.** ModuleBridge — RPC и события. По HTTP допустимы только JSON-примитивы (`user_public_id`, не ORM `request.user`). Итератор (например `ollama_framework.chat_stream`) уходит NDJSON-потоком (`X-Bridge-Stream`), не одним JSON. События — Redis EventBus (fire-and-forget, не синхронный quorum). Согласованность данных на уровне 3 — исходящая очередь владельца + приём без дублей у потребителя.

**На уровне 1 процесс модуля всё ещё видит стек ядра** и ту же БД, поэтому JWT с `device_id` работает как раньше. Режим `MODULE_AUTH_MODE=jwt_claims` (principal из claims, проверка устройства через `session.device_active`) нужен на уровне 3.

**Beat модуля живёт с модулем.** Общий Celery Beat на хосте ядра в `MODULE_RUNTIME=microservice` не планирует модули из `MICROSERVICE_MODULES`: их кода и `.env` на той машине может не быть. Если у модуля есть `api/**/celery_beat_config.py`, на хосте модуля ставят `ergoms start-beat --module=<name>` и OS-службу `--kind=beat`. Worker без `--module` не должен забирать чужие очереди; `ergoms start-worker --module=<name>` слушает все очереди этого модуля.

**Клиент** ходит только на входной прокси (`ERGO_PROXY=nginx` / relative API). Раздельная сборка (`CLIENT_MODULARITY=federated|standalone`) не блокирует пилот API.

Если входной сайт — хост модулей, а Django ядра на другой машине: в `env/nginx.env` задайте `NGINX_API_UPSTREAM=<хост-ядра>:8000`. Общий `/api/` и `/ws/` уйдут туда; `/api/<name>/` по-прежнему на локальные процессы из `MICROSERVICE_MODULES`. В `CLIENT_MODULES` перечислите только модули этого хоста. На ядре в `API_ALLOWED_HOSTS` и `CSRF_TRUSTED_ORIGINS` должен быть публичный origin этого nginx.

Если наоборот люди открывают nginx ядра, а собранный клиент живёт на хосте модулей: на ядре задайте `NGINX_CLIENT_UPSTREAM=<хост-модулей>:80` и выполните `ergoms reload-nginx`. Оболочка (`/`, `/assets/`) пойдёт на тот хост; `/api/` и `/ws/` останутся у ядра. На одном hostname это заменяет локальный `core/client/dist` ядра: страницы модулей, которых нет в той сборке, на этом адресе больше не откроются. Чтобы оба интерфейса жили на одном имени, оставьте местный `dist` и задайте `CLIENT_MODULE_REMOTES` плюс `NGINX_CLIENT_REMOTES_UPSTREAM`: оболочка и локальные модули здесь, страницы микросервисов — `remoteEntry.js` с хоста модулей (`/remotes/<name>/`). Папка `modules/<name>/` на ядре для этого не нужна. В Docker Compose `NGINX_CLIENT_UPSTREAM` не читается.

## Несколько серверов без выноса всего хоста

Монолит на одной машине может звать модуль, который живёт на другой: `MODULE_RUNTIME` на этом хосте остаётся `monolith`, а мост переключается на HTTP только для отсутствующих ops.

1. На хосте-потребителе: `BRIDGE_TRANSPORT=http`, общий `BRIDGE_INTERNAL_TOKEN`, в `BRIDGE_SERVICE_URLS` имя соседа и адрес его Django (внутренняя сеть, не публичный сайт за nginx: снаружи `location /internal/` закрыт). Соседи с loopback или тем же хостом, что у процесса, при `BRIDGE_COLOCATE=auto` вызываются в этом процессе, без HTTP. Другой сервер остаётся HTTP. Выключить: `BRIDGE_COLOCATE=off`.
2. Ключи `BRIDGE_SERVICE_URLS` закрывают `integrations.yaml requires`. Папка `modules/<name>/` на этом хосте не нужна. Не заносите чужой модуль в `MICROSERVICE_MODULES`: это список локальных процессов и location nginx, а не удалённых соседей.
3. На хосте-владельце тот же токен и `BRIDGE_TRANSPORT=http` (или microservice). Служебный мост принимает только loopback и адреса private/link-local, не публичный интернет. Между серверами нужна внутренняя сеть или VPN.
4. Пользователи принадлежат ядру. Оба конца оперируют одним `user.public_id` (общее ядро или синхронизация учёток). В HTTP-мост уходит JSON: `user_public_id` и при наличии `user_id`. Объект ORM `user=` по сети не сериализуется. Провайдер на соседе должен принимать `user_public_id` (старый `user=` / `user_id` остаётся для монолита на одной машине).
5. Числовые `*_id` в потребителе — непрозрачные идентификаторы владельца, не внешние ключи на чужую базу.

Пример: один модуль на этом сервере, другой на соседе — `BRIDGE_SERVICE_URLS=<name>=http://peer.example:8000`. Локальный `MODULE_RUNTIME` не меняйте, пока не выносите процесс с этой машины.

## Пилот

Инфраструктуру сначала проверяют на учебном `module_template` (манифест моста, очередь Celery, federation-entry). Боевой пилот — модуль **без межмодульных FK** и без `integrations.yaml requires`.

Подтверждённый пилот по карте данных: модуль без межмодульных FK и без `integrations.yaml requires` (`ergoms data-inventory` → `score=ready`). Кластер с общими FK выносят последним. Учебный каркас — `module_template` (`ready`).

Карту обновляют командой `ergoms data-inventory` (сканирует `modules/*/api`, имена модулей в ядро не зашиты).

## Карта владения данными

| Сущность | Владелец | Читатели | Связь |
|----------|----------|----------|--------|
| Пользователь, `public_id`, сессии, роли ADP | ядро | модули (чтение UUID / JWT) | событие `core.user_delete`; на уровне 2+ без FK |
| Меню CMS | ядро | клиент через API ядра | миграции модуля → таблицы ядра |
| Аудит, inbox уведомлений | ядро | модули через мост | ops `audit.*`, `notifications.*` |
| Файлы | media_api | модуль хранит путь | как сейчас, не раздавать с module API |
| Домен модуля | схема `m_<name>` | только этот модуль | sync-мост / событие / локальная проекция |
| Поиск | владелец документа | ядро Meilisearch | uid с префиксом имени модуля |
| Realtime | вход WS/SSE у ядра | модуль публикует | Redis/Postgres channel layer при нескольких процессах |

Типичные блокеры выноса (колонка `blockers` в `ergoms data-inventory`):

- `cross_module_fk` — внешний ключ на приложение другого модуля;
- `requires_peer` — `integrations.yaml requires`;
- `auth_user_fk` — FK на `AUTH_USER_MODEL` (нужно заменить на `user_public_id` до `schema.yaml isolated`);
- `menu_data_migration` — не блокер процесса: меню остаётся в ядре;
- `raw_sql` — проверить, нет ли обращения к чужим таблицам.

## Этап 1 — split процессов

Цель: пилот живёт отдельным API за nginx на хосте и в Docker; ядро не падает, если модуль недоступен.

1. В `.env`: `MODULE_RUNTIME=microservice`, `MICROSERVICE_MODULES=<name>`, `BRIDGE_TRANSPORT=http`, `BRIDGE_EVENT_BUS=redis`, `BRIDGE_SERVICE_URLS`, `BRIDGE_CORE_URL`, `<NAME>_PORT`. Токен `BRIDGE_INTERNAL_TOKEN` обязателен вне DEBUG.
2. У модуля — `api/bridge_manifest.yaml`. Без файла `ergoms core-rules-check` падает, если имя есть в `MICROSERVICE_MODULES`.
3. Запуск: `ergoms start-module --module=<name>`, `ergoms start-worker --module=<name>`, при периодических задачах — `ergoms start-beat --module=<name>`. OS-службы: `ergoms install-module-service --module=<name> --kind=api|worker|beat` (или hook `host_lifecycle.yaml`) — `install-services` ставит их только при `MODULE_RUNTIME=microservice`, имени в `MICROSERVICE_MODULES` и `HOST_PROFILE`, который допускает `module_api` / `module_worker` / `module_beat`.
4. Прокси: `ergoms reload-nginx`. Location `/api/<name>/` не failover’ит на ядро; при 502/503 клиент получает JSON `module_unavailable`.
5. Docker: `ergoms docker-gen-modules` + `ergoms docker-up`. Compose добавляет API и worker модуля; Beat модуля — если в дереве модуля есть `celery_beat_config.py`. Профили `host-api` / `host-media` / `host-beat` (Beat **ядра**) включаются по `HOST_PROFILE`.
6. Откат: `MODULE_RUNTIME=monolith`, `BRIDGE_TRANSPORT=local`. Режим разработки не ломается.

Набор служб на машине задаёт `HOST_PROFILE` в корневом `.env` (`full` | `core` | `modules` | `auto`). `full` — как раньше. На хосте только модулей (nginx смотрит на чужое ядро через `NGINX_API_UPSTREAM`) поставьте `modules` или `auto` и выполните `ergoms install-services`. Критерий отказа — не строка профиля, а `HostProfile.wants`: если хост не хочет службу `api`, `ergoms start-api` / `dev` и Jupyter ядра завершаются с ошибкой; то же для оболочки Vite без `client` и для общего Beat без `--module`, если нет `beat`. `HOST_SERVICES` с явным `api` остаётся запасным выходом. Не трогайте `start-module`, `start-worker --module`, `start-beat --module` и media_api при `HOST_MEDIA=on`. Детали — `HOST_SERVICES`, `HOST_MEDIA`, `HOST_CELERY_WORKERS` в `env/modules.env`.

На хосте, который не обслуживает службу `api` (типичный `HOST_PROFILE=modules`), процесс `module:<name>` сам берёт `MODULE_PROCESS_PROFILE=slim`, если флаг не задан. Явный `full` побеждает. `slim` оставляет JWT, мост, CMS ADP и аудит; лишние URL и WS-стек не монтируются. Дополнительные apps ядра — `MODULE_PROCESS_CORE_EXTRA` или hook `api/process_profile.yaml` (`core_apps`). На хосте ядра (`core` / `full`) процесс модуля по-прежнему грузит весь стек, пока slim не указан явно.

Health процесса модуля — тот же `GET /api/system/ready/` на порту модуля.

## Этап 2 — схема PostgreSQL

Имена: `core` (ядро) и `m_<name>` (модуль). Схемы `public` в приложении нет: ни в `search_path`, ни как место таблиц (каталог PostgreSQL `public` удаляется после переноса). `search_path` процесса модуля: своя схема, затем схемы соседей на этой машине (`BRIDGE_COLOCATE`), затем `core`. Без схем соседей локальный handler моста не видит их таблицы. У монолита чтение идёт через `core` и все `m_*` (JOIN без префикса схемы). Новые таблицы при `ergoms db-migrate` создаются в схеме приложения: перед каждой миграцией `search_path` ставит её схему первой, в том числе в монолите.

Hook: `modules/<name>/api/schema.yaml`:

```yaml
schema: m_<name>
isolated: false
```

`isolated: true` — CI запрещает FK на чужой `app_label` и на `AUTH_USER_MODEL`. Пока у модуля остаются межсхемные ключи, оставляйте `isolated: false`: таблицы всё равно живут в `m_<name>`, JOIN в монолите идёт через `search_path`. Не квалифицируйте `Meta.db_table` схемой — это ломает SQLite.

Команды:

```cmd
ergoms db-migrate-module --module=<name>
ergoms db-move-module-schema --module=<name>
ergoms db-move-module-schema --all
ergoms db-move-core-schema
```

`db-migrate-module` создаёт схему и применяет миграции приложений модуля. `db-move-module-schema` переносит уже существующие таблицы из `public` или `core` в `m_<name>`. `db-move-core-schema` переносит остаток ядра из `public` в `core` и удаляет `public`. `--all` делает оба шага. Права: `ergoms db-migrate-module --module=<name> --grant-role=<pg_role>` — `USAGE` на `core`, `ALL` на `m_<name>`.

На SQLite схемы — no-op: таблицы остаются в одной файловой БД.

## Этап 3 — контракты, события, вход

- Ops моста версионируйте именем (`op.v2`) или полем `version` в манифесте. В CI — `BRIDGE_CONTRACTS=raise`.
- Событие удаления пользователя: JSON `user_id`, `user_public_id`, `username` (не ORM). Подписчик пилота чистит строки по `user_public_id`.
- Исходящая очередь: модели `OutboxEvent` / `InboxEvent`, задача `core.integrations.flush_outbox` в Beat. Перед уровнем 3 outbox обязателен; на уровне 2 допустим Redis + идемпотентный ключ.
- JWT выдаёт только ядро. Модуль проверяет подпись тем же ключом и не доверяет `user_id` из тела запроса. На уровне 3 — `MODULE_AUTH_MODE=jwt_claims`.
- Сквозной `X-Request-ID`: прокси → API → мост → Celery (`ergo_request_id` в заголовках задачи).

## Этап 4 — Celery, файлы, realtime, поиск

- Очередь по умолчанию = имя папки. Вложенные apps могут объявить свои очереди; `ergoms start-worker --module=<name>` ставит `-Q` на все очереди маршрутов `modules.<name>.*` и `PROCESS_MODULES`.
- Расписание — `api/**/celery_beat_config.py`. На хосте модуля: `ergoms start-beat --module=<name>` (свой файл `celerybeat-schedule-<name>`). Общий Beat ядра эти задачи не дублирует.
- Файлы — как media_api: модуль хранит путь, байты не отдаёт с module API.
- Realtime: вход WS/SSE у ядра; модуль только публикует. Несколько процессов — Redis или Postgres channel layer.
- Поиск: хук `api/search_indexes.py`, `uid` с префиксом имени модуля (`<name>_stored_video`).

## Этап 5 — отдельная БД (желательно)

В `databases.yaml`:

```yaml
  module_pilot:
    engine: "postgresql"
    name: "ergo_ms_module_pilot"
    module: "<name>"
    user: ""
    password: ""
    host: "127.0.0.1"
    port: 5433
```

Поле `module:` включает router `app_label → alias`. Миграции: `ergoms db-migrate-module --module=<name> --database=module_pilot`.

Бэкап схемы (уровень 2): дамп схемы `m_<name>`. Бэкап базы (уровень 3): дамп alias целиком. Восстановление — на пустую схему/БД, затем `ergoms db-migrate-module`. Падение БД пилота не должно валить процесс ядра (отдельный alias, деградация `/api/<name>/`).

На уровне 3 модуль не читает `auth_user`: principal из JWT, `device_id` — op `session.device_active`. Ответ — `False` или снимок `{active, user_public_id, username, is_superuser, is_staff}`: в токене старого логина может не быть `user_public_id`.

## Этап 6 — клиент и сопровождение

- Federated remote: на хосте, где лежит `modules/<name>/client`, достаточно `ergoms client-build` — команда соберёт remotes из `MICROSERVICE_MODULES` / `CLIENT_MODULE_REMOTES` и пропустит оболочку ядра, если этот хост её не отдаёт. Явно один модуль: `ergoms client-build --module=<name>` или `ergoms client-build-remote --module=<name>`. Свой `federation-entry.js` не обязателен: сборка соберёт его из hook-файлов. Если remote недоступен, оболочка продолжает работу (предупреждение в логе, не белый экран).
- Standalone SPA: `ergoms client-build-standalone --module=<name>`.
- В логах смотрите поля `service` / `module` и `request_id`.
- Следующий модуль выносят по чеклисту ниже; живой эталон файлов — `modules/module_template/`.

## Чеклист выноса следующего модуля

1. `ergoms data-inventory` — нет `cross_module_fk` и `requires_peer`.
2. Снять FK на `AUTH_USER_MODEL` → `user_public_id`; каскад удаления — подписка на `core.user_delete`.
3. `api/bridge_manifest.yaml`, `api/schema.yaml` (`isolated: true`).
4. `host_lifecycle.yaml` + `process_roles.yaml` (API, worker и при наличии расписания Beat через `install-module-service`; службы ставятся только в microservice-режиме).
5. Worker только с `--module=`; очереди — все, что объявил модуль. Beat модуля — `--kind=beat`, не общий Beat ядра.
6. Файлы через media_api; поиск — `search_indexes.py` с префиксом uid.
7. Прогон: monolith не сломан; microservice на хосте и `ergoms docker-gen-modules`.
8. Клиент: 503 модуля не роняет оболочку; при federation — запасной UI.
9. `ergoms core-rules-check` без ошибок границ данных и манифеста.

## Сознательно не делаем

Вынос всех модулей одним релизом; обязательный Kubernetes; переписывание домена «ради архитектуры»; замена ModuleBridge на другой RPC.
