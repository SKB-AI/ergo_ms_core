# Настройка конфигурации

Перед первым запуском в корне проекта нужны **`.env`**, при необходимости **`env/nginx.env`** / **`env/docker.env`**, и **`databases.yaml`**. При полной первичной настройке (`setup-full`) они создаются из примеров автоматически; после этого их нужно проверить и при необходимости настроить под свою среду.

## Файл `.env` и фрагменты `env/`

Если файла ещё нет, скопируйте `.env.example` в `.env`. Режимы работы задаются четырьмя переключателями:

```env
ERGO_RUNTIME=host
ERGO_PROXY=none
ERGO_BROKER=local
ERGO_DB=portable_postgres
```

| Переключатель | Значения | Смысл |
|---------------|----------|--------|
| `ERGO_RUNTIME` | `host` \| `docker` | процессы на машине или Docker Compose |
| `ERGO_PROXY` | `none` \| `nginx` | прямой доступ или reverse proxy (`env/nginx.env`) |
| `ERGO_BROKER` | `local` \| `redis` | locmem/SQLite Celery или Redis из `databases.yaml` |
| `ERGO_DB` | `sqlite` \| `postgres` \| `portable_postgres` \| `mysql` \| `mssql` | engine секции `default` |
| `ERGO_JUPYTER` | `none` \| `auto` \| `local` \| `lan` \| `nginx` | JupyterLab; детали — `env/jupyter.env` |
| `ERGO_EMAIL` | `none` \| `smtp` | исходящая почта; SMTP — `env/smtp.env` |
| `ERGO_MEDIA` | `local` \| `remote` | доступ core/api к файлам; детали — `env/media.env` |
| `ERGO_REALTIME` | `websocket` \| `sse` \| `http_polling` | транспорт событий клиенту; детали — `env/realtime.env` |
| `ERGO_SEARCH_ENABLED` | `true` \| `false` | Meilisearch / BM25; детали — `env/search.env` |
| `ERGO_ENV` | `development` \| `production` (коротко `dev` \| `prod`) | режим API, клиента и media_api |

Детали — шаблоны в [`env/`](../env/) (`*.env.example`: nginx, docker, jupyter, smtp, logging, mcp, media, realtime, cache, celery, search). Порядок загрузки: `.env` → `env/*.env` → `modules/**/.env`.

Для локальной разработки обычно хватает режимов по умолчанию и минимального набора API/CLIENT (см. `.env.example`). Почту можно не настраивать при `ERGO_EMAIL=none`.

При развёртывании на сервере смените `API_SECRET_KEY`, выставьте `ERGO_ENV=production`, при необходимости `ERGO_PROXY=nginx` и правьте `env/nginx.env`.

## Файл `databases.yaml`

Если файла ещё нет, скопируйте `databases.yaml.example` в `databases.yaml`. Для начала работы нужна хотя бы секция **`default`** — основная база приложения. Engine секции `default` при заданном `ERGO_DB` выставляет loader (`portable_postgres` → `postgresql`); host/port/name задаёте здесь. При `ERGO_DB=portable_postgres` пустые или шаблонные `user`/`password` заполняются уникальными значениями при `setup-full` / `install-postgres`, пока кластер ещё не создан.

```yaml
databases:
  default:
    engine: "postgresql"
    name: "ergo_ms"
    user: "postgres"
    password: ""
    host: "127.0.0.1"
    port: 5433

  redis:
    engine: "redis"
    host: "127.0.0.1"
    port: 6379
    user: ""
    password: ""
    db_channel: 0
    db_cache: 1
    db_celery_broker: 2
    db_celery_result: 3
```

Секция **`redis`** нужна при `ERGO_BROKER=redis` (кэш, channel layer, Celery). Если её ещё нет в уже существующем `databases.yaml`, `setup-full`, `install-redis` и запись секретов режимов дописывают её из example, не затирая `default`. Пустые `user`/`password` заполняются уникальными значениями, пока в `redis.conf` нет `requirepass`; затем conf пересобирается из yaml. Дополнительные SQL-базы — новые секции под `databases:` (как `analytics`).

Убедитесь, что основная база создана и пользователь имеет к ней доступ. После правок примените миграции: `ergoms db-migrate`.

### Базы для Celery

Фоновые задачи могут использовать ту же PostgreSQL или отдельные секции в том же файле:

- **`celery`** — общая очередь; удобный вариант для разработки, когда worker и beat делят одну базу.
- **`celery_worker`** и **`celery_beat`** — раздельные настройки, если на сервере нужна изоляция очередей.

Важно: **worker и beat должны подключаться к одному брокеру**. Иначе планировщик будет ставить задачи в одну очередь, а исполнитель слушать другую. Если секции Celery не заданы, система создаст локальный SQLite в `virtual_env/celery/` — этого достаточно для экспериментов, но не для нагрузки.

При локальной разработке можно указать одного пользователя `postgres` во всех секциях — так проще, чем заводить отдельных пользователей БД.

### Замечание про YAML

Если в пароле или других строках встречаются символы `!`, `#`, `{`, `}` — заключите значение в двойные кавычки, например: `password: "!admin123"`. Иначе парсер YAML может прочитать строку неверно.

## Меню приложения

Пункты бокового меню хранятся в БД. Модуль с клиентом регистрирует их в **миграции данных API** (`MenuMigrationHelper`, `module_source='modules/<имя>'`), затем выполняет `ergoms migrate-all`. Маршруты страниц — в `client/js/routes.js`; каждый пункт меню ссылается на `routeName` из этого файла.

Порядок пунктов и видимость для пользователей настраиваются в админ-панели CMS (`MenuPanel.vue`) и тоже сохраняются в БД. Подробнее — [architecture.md](architecture.md#боковое-меню) и [`.cursor/rules/menu.mdc`](../.cursor/rules/menu.mdc).

## Регистрация пользователей

Режим задаётся переменной **`API_REGISTRATION_MODE`** в `.env`:

| Значение | Поведение |
|----------|-----------|
| `open` | свободная регистрация (по умолчанию) |
| `invitation` | только по приглашению глобального администратора |
| `closed` | регистрация отключена |

Для режима `invitation` также задайте **`API_REGISTRATION_INVITATION_TTL_DAYS`** (срок действия ссылки). Управление приглашениями — в CMS, раздел «Приглашения». Проверка уникальности email — **`API_REGISTRATION_CHECK_EMAIL_EXISTS`**.

Самостоятельное изменение email, ФИО и телефона в профиле — **`API_USER_PROFILE_SELF_EDIT_ENABLED`**. Если выключено, пользователи отправляют заявки администратору.

## Настройки клиента

Все параметры клиента — в `.env` с префиксом **`CLIENT_*`** или через общие переменные **`API_*`**, **`DISABLED_MODULES`**, **`REALTIME_*`** (фрагмент `env/realtime.env`). Сборка (`core/client/vite.config.js`) подставляет их в бандл; в коде используйте **`@/js/clientEnv.js`**, не `import.meta.env` напрямую.

| Переменная в `.env` | Назначение |
|---------------------|------------|
| `CLIENT_LOG_LEVEL` | Уровень логов клиента |
| `CLIENT_USE_RELATIVE_API` | обычно из `ERGO_PROXY=nginx`; override — `env/nginx.env` |
| `API_HOST`, `API_PORT` | Адрес API в dev без nginx |
| `API_PASSWORD_*` | Политика паролей (сервер и подсказки в формах) |
| `DISABLED_MODULES` | Отключённые модули (сервер и клиент) |

Новая настройка модуля для клиента: `CLIENT_<МОДУЛЬ>_*` в `modules/<имя>/.env` (или в корневом `.env`) + поле в `clientEnv.js` и `buildClientEnvDefines()` в `vite.config.js`.

## Настройки модулей

У части модулей есть собственные `.env.example` в каталоге `modules/<имя>/`. При `setup-full` из них создаётся `modules/<имя>/.env`. Переменные из модульных `.env` **переопределяют** одноимённые из корневого `.env` — и на сервере (Django), и при сборке клиента (Vite).

Список ключей — в `modules/<имя>/.env.example` каждого модуля, не в ядре. Имена установленных модулей сюда не копируют.

Если модуль указан в `DISABLED_MODULES`, его `.env` не учитывается при сборке клиента, Python/npm-зависимости не устанавливаются (`ergoms python-install`, `ergoms npm run install:all`), пункты меню скрываются в API, `restore_menu` не наполняет меню модуля; в Docker build context каталог `modules/<имя>/` исключается через `Dockerfile.*.dockerignore` (корневой `.dockerignore` не меняется).

## Realtime (WebSocket, SSE и polling)

Переменные **`REALTIME_*`** — в [`env/realtime.env`](../env/realtime.env.example), **единые для сервера и клиента**. После входа клиент синхронизируется с `GET /api/realtime/config/`.

- `websocket` — Django Channels (по умолчанию); API через ASGI.
- `sse` — push через **Server-Sent Events** (`GET /api/realtime/stream/`).
- `http_polling` — клиент опрашивает REST по интервалам (`GET /api/realtime/sync/`).

**Channel layer** (для push между worker’ами): `CHANNEL_LAYER_BACKEND=memory` (dev, один процесс), `postgres` (без Redis, через основную БД) или `redis`. См. [`env/realtime.env.example`](../env/realtime.env.example).

Интервалы polling (секунды): `REALTIME_POLL_*`. SSE keepalive: `REALTIME_SSE_KEEPALIVE_INTERVAL`.

Nginx: отдельный location для `/api/realtime/stream/` — `core/deployment/nginx/ergo_ms.conf.template`. Правила разработки — [`.cursor/rules/realtime.mdc`](../.cursor/rules/realtime.mdc).

## Кэш Django

Переменная **`API_CACHE_BACKEND`** в [`env/cache.env`](../env/cache.env.example):

| Значение | Когда |
|----------|-------|
| `locmem` | разработка, один процесс API (по умолчанию) |
| `file` | без Redis на Linux (не рекомендуется на Windows) |
| `redis` | несколько процессов, общий кэш |

При `ERGO_BROKER=redis` без явного `API_CACHE_BACKEND` effective-backend — `redis` (см. [`redis_runtime.py`](../core/api/src/config/redis_runtime.py)).

## Celery broker

Помимо секций в `databases.yaml` (SQLite/PostgreSQL) в `.env` задаётся **`CELERY_BROKER_BACKEND`**:

| Значение | Поведение |
|----------|-----------|
| `auto` | из `databases.yaml` или локальный SQLite |
| `redis` | брокер на Redis (`CELERY_BROKER_URL` или `REDIS_DB_CELERY_BROKER`) |
| `database` | явно через БД |
| `local` | локальный SQLite в `virtual_env/celery/` |

Расписание Beat (django-celery-beat) хранится в PostgreSQL, не в Redis.

## Redis и несколько процессов {#redis-и-несколько-процессов}

Portable Redis при `ERGO_BROKER=redis` ставит `setup-full` (шаг `EnsureRedisStep`). Вручную:

```cmd
ergoms install-redis
ergoms test-redis
```

В `.env`: **`ERGO_BROKER=redis`**, параметры подключения — секция `redis` в `databases.yaml`, перезапустите API.

При `ERGO_BROKER=redis` модуль [`redis_runtime.py`](../core/api/src/config/redis_runtime.py) по умолчанию включает `redis` для channel layer, кэша и Celery broker — если не заданы явно `CHANNEL_LAYER_BACKEND`, `API_CACHE_BACKEND`, `CELERY_BROKER_BACKEND`.

Типичный набор для production (см. блок «Несколько процессов» в `.env.example`):

```env
ERGO_BROKER=redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
API_CACHE_BACKEND=redis
CHANNEL_LAYER_BACKEND=redis
CELERY_BROKER_BACKEND=redis
```

Разделение баз Redis по умолчанию: DB 0 — channel layer, 1 — кэш, 2 — Celery broker, 3 — result backend (`REDIS_DB_*`).

Альтернатива Redis для channel layer — **`CHANNEL_LAYER_BACKEND=postgres`** (через основную БД).

Подробнее — [`core/deployment/logic.md`](../core/deployment/logic.md#redis-optional-portable-packages), [cli.md](cli.md#redis-опционально).

## Production за nginx

Эталон переменных для запуска за обратным прокси — [`env/nginx.env.example`](../env/nginx.env.example) при `ERGO_PROXY=nginx` в корневом `.env`.

Ключевые переменные:

| Переменная | Назначение |
|------------|------------|
| `ERGO_PROXY=nginx` | включить сценарий nginx; `setup-full` ставит portable nginx; детали — `env/nginx.env` |
| `NGINX_SERVER_NAME`, `NGINX_PUBLIC_HOST` | домен |
| `CLIENT_USE_RELATIVE_API` | при `ERGO_PROXY=nginx` — true; иначе override в `env/nginx.env` |
| `ERGO_ENV` | `production` (или legacy `API_DEPLOY_TYPE` / `CLIENT_DEPLOY_TYPE`) |
| `ERGO_TLS_*`, `ERGO_SSL_CERT`, `ERGO_SSL_KEY` | TLS (Linux, `ergoms install-tls`) |

Команды: `ergoms install-nginx`, `ergoms reload-nginx`, `ergoms test-nginx` — см. [cli.md](cli.md#nginx-опционально).

## Docker Compose

Переменные **`DOCKER_*`** — [`env/docker.env.example`](../env/docker.env.example), не корневой `.env`. Режим стека — `ERGO_RUNTIME=docker` в корневом `.env`. Порты публикуются из `API_PORT`, `CLIENT_PORT`, `MEDIA_API_BIND_PORT` и др.; параметры БД — из `databases.yaml` (при `DOCKER_DATABASE=container` хост `localhost` подменяется на сервис `postgres` внутри compose).

В `env/docker.env` задайте режим `DOCKER_MODE` (`dev` / `prod`) и profiles (`DOCKER_PROFILE_POSTGRES`, `DOCKER_PROFILE_NGINX`, `DOCKER_PROFILE_JUPYTER`). Первый запуск: `ergoms docker-init`.

Скрипты не изменяют корневой `.env` — генерируют только артефакты в `core/deployment/docker/`. Подробнее — [docker.md](docker.md).

## GeoIP (геолокация IP)

Локальная база DB-IP City Lite для city/country в сессиях и аудите. В `.env`:

- **`GEOIP_ENABLED=true`** — включить lookup при входе и в аудите
- **`GEOIP_DOWNLOAD_URL`** — URL архива MMDB (см. `.env.example`)

Скачать базу: `ergoms geoip-download`. Заполнить старые записи: `ergoms geoip-backfill`. Подробнее — [`core/deployment/logic.md`](../core/deployment/logic.md#geoip-db-ip-city-lite).

## Совместная работа через Live Share

Если нужно показать проект коллеге в реальном времени, подойдёт расширение [Live Share](https://marketplace.visualstudio.com/items?itemName=MS-vsliveshare.vsliveshare) для VS Code или Cursor. Доступ гостя задаёт [`.vsls.json`](../.vsls.json): всё из `.gitignore` закрыто полностью (не только скрыто в дереве), плюс явно исключены рабочие `.env`, `env/*.env`, `databases.yaml`, ключи TLS и `redis.conf`. Шаблоны `*.example` гостю видны. У участников сеанса должны быть доступны порты API и клиента из вашего `.env` — обычно это 8000 и 8001.

## См. также

| Вопрос | Документ |
|--------|----------|
| Справочник команд ergoms | [cli.md](cli.md) |
| Если конфигурация не применяется | [troubleshooting.md](troubleshooting.md) |
| Запуск для разработки | [development.md](development.md) |
| Docker Compose | [docker.md](docker.md) |
