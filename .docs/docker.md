# Docker Compose

ERGO MS можно запустить в контейнерах через **Docker Compose** — альтернатива portable Redis/nginx на хосте и отдельным процессам `ergoms dev`. Команды — префикс **`ergoms docker-*`**; конфигурация — переменные **`DOCKER_*`** в корневом `.env` и параметры **`databases.yaml`**.

Справочник команд — [cli.md](cli.md#docker-compose). Устройство скриптов — [`core/deployment/logic.md`](../core/deployment/logic.md#docker-compose). Правила для агента — [`.cursor/rules/docker.mdc`](../.cursor/rules/docker.mdc).

## Предварительные условия

На компьютере должны быть установлены:

- **Docker Desktop** (Windows, macOS) или **Docker Engine** + **Compose V2** (Linux)
- **Git** и клонированный репозиторий с submodules (`git submodule update --init --recursive`)
- Для первого запуска — выполненная первичная настройка окружения (`setup-full`) или как минимум `ergoms python-install`, чтобы на хосте был `virtual_env/python/` для CLI `docker_cli.py`

Путь к проекту — только латиница, цифры, дефис и подчёркивание (как в [README.md](../README.md)).

## Первый запуск

1. Скопируйте `.env` из `.env.example`, если файла ещё нет. В секции **Docker** задайте:

   - `DOCKER_ENABLED=true`
   - `DOCKER_MODE=dev` (разработка) или `prod` (запуск как на сервере)
   - при необходимости profiles: `DOCKER_PROFILE_POSTGRES`, `DOCKER_PROFILE_NGINX`, `DOCKER_PROFILE_JUPYTER`

2. Проверьте **`databases.yaml`**. При `DOCKER_DATABASE=container` (по умолчанию) хост `localhost` / `127.0.0.1` в yaml автоматически заменяется на имя сервиса `postgres` внутри сети compose. Параметры пользователя, пароля и имени БД берутся из секции `default`.

3. Выполните полную инициализацию стека:

   ```cmd
   ergoms docker-init
   ```

   Команда собирает образы, генерирует worker-сервисы из `celery_workers.yaml`, поднимает контейнеры, устанавливает Python-зависимости модулей и применяет миграции. Оркестрация — [`core/deployment/lifecycle/`](../core/deployment/lifecycle/) (`DeploymentOrchestrator`); `docker_cli.py` — тонкий CLI.

   Модули из `DISABLED_MODULES` не попадают в Docker build context
   (`Dockerfile.*.dockerignore` рядом с Dockerfile, генерируется при prepare;
   корневой `.dockerignore` не меняется). Их зависимости не ставятся при
   `api install` / npm в bootstrap.

4. Откройте приложение:

   | Режим | Куда заходить |
   |-------|----------------|
   | `dev`, без nginx | `http://localhost:<CLIENT_PORT>` (по умолчанию 8001) |
   | `dev`, API напрямую | `http://localhost:<API_PORT>` (по умолчанию 8000) |
   | `prod` или profile nginx | `http://localhost:<NGINX_LISTEN_PORT>` (по умолчанию 80) |

Порты публикуются из существующих ключей `.env`: `API_PORT`, `CLIENT_PORT`, `MEDIA_API_BIND_PORT`, `NGINX_LISTEN_PORT`, `API_JUPYTER_BIND_PORT` — отдельные `DOCKER_*_PORT` не нужны.

## Режимы

### dev (`DOCKER_MODE=dev` или `ergoms docker-dev`)

- Исходники монтируются в контейнеры (**bind-mount** корня проекта)
- API — `runserver` через `start_api.py`
- Клиент — **Vite dev** в сервисе `client`
- Порты API, media_api и клиента проброшены на хост

Удобно для разработки на машине, где не хочется держать Redis, PostgreSQL и несколько терминалов вручную.

### prod (`DOCKER_MODE=prod` или `ergoms docker-prod`)

- API и media_api — production-тип развёртывания (`daphne`)
- Одноразовый сервис **`client-build`** собирает `core/client/dist`
- Прямые порты API и media_api на хост **не** публикуются — доступ через nginx (profile) или внутреннюю сеть

Перед `docker-prod` с nginx убедитесь, что `DOCKER_PROFILE_NGINX=true` и в `.env` заданы `NGINX_SERVER_NAME`, `CLIENT_USE_RELATIVE_API=true`.

## Состав стека

Базовый файл — `core/deployment/docker/docker-compose.yml`:

| Сервис | Назначение |
|--------|------------|
| `redis` | Кэш, channel layer, брокер Celery |
| `api` | Django API |
| `media-api` | Media API |
| `celery-beat` | Планировщик Celery |

Дополнительные фрагменты подключаются по **profiles** и режиму:

| Profile / режим | Файл | Сервисы |
|-----------------|------|---------|
| `postgres` | `docker-compose.postgres.yml` | PostgreSQL 16 |
| `nginx` | `docker-compose.nginx.yml` | nginx (единая точка входа) |
| `jupyter` | `docker-compose.jupyter.yml` | JupyterLab |
| `dev` | `docker-compose.dev.yml` | `client` (Vite) |
| `prod` | `docker-compose.prod.yml` | `client-build` |
| workers | `docker-compose.workers.generated.yml` | Celery worker'ы из `celery_workers.yaml` |

Образы:

- **`ergo_ms-python:local`** — `Dockerfile.python` (API, media_api, Celery, Jupyter)
- **`ergo_ms-client:local`** — `Dockerfile.client` (Node 20, Vite / сборка)

## Команды

```cmd
ergoms docker-init
ergoms docker-clean --yes
ergoms docker-up
ergoms docker-down
ergoms docker-restart
ergoms docker-dev
ergoms docker-prod
ergoms docker-build
ergoms docker-ps
ergoms docker-logs -f api
ergoms docker-migrate
ergoms docker-install-deps
ergoms docker-install-npm
ergoms docker-shell-api
ergoms docker-gen-workers
```

- **`docker-clean --yes`** — остановить стек, удалить тома и локальные образы, очистить `.compose.env` и прочие артефакты в `core/deployment/docker/` (данные PostgreSQL в томе будут потеряны)
- **`docker-migrate`** — `migrate`, `warmup_caches` внутри контейнера `api` (без `makemigrations`; новые миграции — `ergoms db-makemigrations` на хосте или `ergoms migrate-all`)
- **`docker-install-deps`** — Python-зависимости (poetry) внутри контейнера `api`
- **`docker-install-npm`** — npm-зависимости внутри стека Docker
- **`docker-gen-workers`** — пересоздать `docker-compose.workers.generated.yml` после правки `celery_workers.yaml`
- **`docker-shell-api`** — интерактивная shell в контейнере API; внутри доступны `ergoms api …` и `ergoms media_api …` (тонкая обёртка, не полный CLI хоста)

Миграции и Django-команды в контейнере — через `ergoms docker-migrate` или `ergoms docker-shell-api` (например `ergoms api createsuperuser`), не `python manage.py` на хосте.

Если интерактивный `createsuperuser` в Windows-терминале падает с ошибкой кодировки UTF-8, создайте пользователя без запросов:

```bash
export DJANGO_SUPERUSER_USERNAME=admin
export DJANGO_SUPERUSER_PASSWORD='your-password'
export DJANGO_SUPERUSER_EMAIL=admin@example.com
ergoms api createsuperuser --noinput
```

## Переменные Docker

Секция в [`.env.example`](../.env.example) (строки `DOCKER_*`). Основные:

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `DOCKER_ENABLED` | `false` | Режим Docker (effective env в `.compose.env`) |
| `DOCKER_MODE` | `dev` | `dev` или `prod` |
| `DOCKER_DATABASE` | `container` | `container` — PostgreSQL в compose; `host` — БД на хосте |
| `DOCKER_PROFILE_POSTGRES` | `true` | Поднять PostgreSQL в контейнере |
| `DOCKER_PROFILE_NGINX` | `false` | nginx в Docker |
| `DOCKER_PROFILE_JUPYTER` | `false` | JupyterLab в Docker |
| `DOCKER_COMPOSE_PROJECT` | `ergo_ms` | Имя проекта compose (префикс томов) |
| `DOCKER_VOLUME_LOGS` | `bind` | `bind` — каталог `logs/` проекта; иначе named volume |
| `DOCKER_VOLUME_MEDIA` | `bind` | `bind` — каталог `media/` проекта |
| `DOCKER_VOLUME_CELERY_CACHE` | `named` | `named` — том Docker; `bind` — `virtual_env/cache` на хосте |
| `DOCKER_BUILD_CACHE` | `true` | BuildKit при `ergoms docker-build` |
| `DOCKER_DEPS_CACHE` | `internal` | `internal` / `project` (ещё `virtual_env/docker-cache/`) / `off` — кэш загрузок Poetry/npm |
| `DOCKER_BUILD_POLICY` | `if-missing` | `if-missing` — пропуск build в `docker-init`, если образы есть; `always` — всегда |
| `DOCKER_NPM_INSTALL` | `smart` | `smart` — npm только при изменении lock/package.json; `always` — каждый старт client |

При `DOCKER_DATABASE=host` в `databases.yaml` укажите хост, доступный из контейнера (на Docker Desktop — часто `host.docker.internal`).

Скрипты **не записывают** в корневой `.env` — см. [configuration.md](configuration.md). Генерируются только артефакты в `core/deployment/docker/` (в `.gitignore`).

## Артефакты compose (не коммитить)

| Файл | Назначение |
|------|------------|
| `.compose.env` | merged `.env` + runtime-overrides для контейнеров |
| `.compose.databases.yaml` | `databases.yaml` с подставленным хостом postgres |
| `docker-compose.workers.generated.yml` | Celery worker'ы |
| `docker-compose.build.generated.yml` | local BuildKit cache (`DOCKER_DEPS_CACHE=project`) |
| `init/postgres/02-celery-databases.sql` | доп. БД Celery при первом старте postgres |
| `nginx/ergo_ms.conf.rendered` | конфиг nginx для Docker |

Логика генерации — [`docker_runtime.py`](../core/deployment/docker/docker_runtime.py), CLI — [`docker_cli.py`](../core/deployment/docker/docker_cli.py).

## Тома и данные

- **Код проекта** — bind-mount всего корня в `/app` (изменения на хосте видны в контейнере)
- **`poetry_venv`**, **`node_modules`** (том на `/app/virtual_env/npm/node_modules`), **`npm_cache`** — named volumes (ускоряют повторный старт)
- **`celery_cache`** — named volume или bind `virtual_env/cache` (`DOCKER_VOLUME_CELERY_CACHE=bind`)
- **`postgres_data`** — данные PostgreSQL (profile postgres)
- **`logs/`** и **`media/`** — по умолчанию bind на хост (`DOCKER_VOLUME_*=bind`)

Журналы API по-прежнему пишутся в `logs/` на хосте при bind-режиме.

## Кэш зависимостей

Сборка образов (`Dockerfile.python`, `Dockerfile.client`):

- слой Poetry — только `pyproject.toml` + `poetry.lock` (`poetry install --no-root`); полная установка ядра и модулей — **`api install`** при `docker-init`;
- npm — **`ensure_npm_deps.sh`** при старте client (`npm run install:all` в `virtual_env/npm`, как `ergoms setup-full`); зависимости хранятся в томах `node_modules` → `/app/virtual_env/npm/node_modules` и `npm_cache`.

| Режим `DOCKER_DEPS_CACHE` | Поведение |
|---------------------------|-----------|
| `internal` (по умолчанию) | BuildKit cache mount (wheel/npm внутри Docker) |
| `project` | дополнительно каталог `virtual_env/docker-cache/` |
| `off` | без cache mount — каждый build качает пакеты заново |

**Скачать всё заново:**

```env
DOCKER_DEPS_CACHE=off
DOCKER_BUILD_POLICY=always
DOCKER_NPM_INSTALL=always
```

```cmd
ergoms docker-build -- --no-cache
ergoms docker-init
```

Очистка: удалите `virtual_env/docker-cache/`, тома `*_poetry_venv`, `*_node_modules` или выполните `ergoms docker-clean --yes`. Internal BuildKit-кэш — `docker builder prune` (вручную).

## PostgreSQL в контейнере

При `DOCKER_DATABASE=container` и `DOCKER_PROFILE_POSTGRES=true`:

1. Параметры контейнера (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) берутся из `databases.yaml` → `default`
2. Порт на хост — только если свободен (`port` из `default`, часто 5432). Если занят (системный PostgreSQL и т.п.), публикация пропускается; контейнеры ходят по сети compose на `postgres:5432`. Принудительно: `DOCKER_POSTGRES_PUBLISH_PORT` (`none` или другой порт)
3. Дополнительные БД для Celery (`celery`, `celery_worker`, `celery_beat`) создаются скриптом `02-celery-databases.sql` при первой инициализации тома

Перед сменой пароля или имени БД в уже инициализированном томе может понадобиться `ergoms docker-down` с удалением volume `*_postgres_data` — **это удалит данные**.

## nginx в Docker

При `DOCKER_PROFILE_NGINX=true`:

- Шаблон — `core/deployment/docker/nginx/ergo_ms.docker.conf.template`
- Upstream — **имена сервисов** compose (`api`, `media-api`), не `127.0.0.1`
- Статика клиента — `core/client/dist` (в prod после `client-build`)
- На хост обычно публикуется только `NGINX_LISTEN_PORT`

Не включайте одновременно portable nginx на хосте (`ergoms install-nginx`) и profile nginx в Docker без смены портов — возможен конфликт.

## Entrypoint и ожидание сервисов

Образ Python использует [`docker_entrypoint.sh`](../core/deployment/docker/entrypoint/docker_entrypoint.sh):

1. [`wait_for_services.py`](../core/deployment/docker/entrypoint/wait_for_services.py) ждёт Redis и PostgreSQL (если profile postgres)
2. Подставляет `PATH` к Poetry venv
3. Выполняет `command` сервиса из compose

Таймаут ожидания — `ERGO_DOCKER_WAIT_TIMEOUT` (по умолчанию 120 с) в effective env.

## Типичные ошибки

| Симптом | Что проверить |
|---------|----------------|
| `Docker не найден` | Установлен Docker Desktop / `docker compose version` в PATH |
| Таймаут PostgreSQL | Контейнер `postgres` в `ergoms docker-ps`; логи: `ergoms docker-logs postgres` |
| API не видит БД | `databases.yaml` и `.compose.databases.yaml`; режим `DOCKER_DATABASE` |
| Порт занят | `API_PORT`, `CLIENT_PORT`, `NGINX_LISTEN_PORT` в `.env`; другой экземпляр ERGO MS или portable-службы |
| Нет worker'ов | `celery_workers.yaml` на месте; `ergoms docker-gen-workers` и `ergoms docker-restart` |
| nginx 502 в prod | Выполнен `client-build`; есть `core/client/dist` |
| Конфликт Redis | В Docker `REDIS_HOST` переопределяется на `redis`; не смешивайте с `ergoms install-redis` на том же порту |

## Docker и локальная разработка без контейнеров

| Сценарий | Рекомендация |
|----------|--------------|
| Обычная разработка на хосте | `ergoms dev`, `ergoms start-client` — [development.md](development.md) |
| Изолированный стек, CI, демо | `ergoms docker-dev` |
| Запуск как на сервере в контейнерах | `ergoms docker-prod` + profile nginx |
| Службы ОС (systemd / NSSM) | [deployment.md](deployment.md) |

Одновременно не запускайте те же порты и два способа Redis/nginx (хост + Docker).

## См. также

| Вопрос | Документ |
|--------|----------|
| Команды ergoms | [cli.md](cli.md#docker-compose) |
| `.env`, `databases.yaml` | [configuration.md](configuration.md) |
| Portable Redis / nginx на хосте | [cli.md](cli.md#nginx-опционально), [`deployment-infra.mdc`](../.cursor/rules/deployment-infra.mdc) |
| Системные службы | [deployment.md](deployment.md) |
| Устройство ergoms | [`core/deployment/logic.md`](../core/deployment/logic.md) |
| Ошибки установки | [troubleshooting.md](troubleshooting.md) |
