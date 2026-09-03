# Устройство развёртывания (ergoms)

Этот документ описывает, как устроены скрипты в `core/deployment/` и утилита **ergoms** — единая точка входа для установки, запуска и служебных команд ERGO MS. Его читают разработчики, которые добавляют команды или правят скрипты под Windows и Linux.

Справочник команд для людей — в [`.docs/cli.md`](../../.docs/cli.md). Технический формат `commands.conf` — в [`.cursor/rules/ergoms-commands.mdc`](../../.cursor/rules/ergoms-commands.mdc).

## Предварительные условия

- Репозиторий клонирован; путь к проекту содержит только латиницу, цифры, дефис и подчёркивание.
- **Portable Python 3.12** и **Node.js LTS** ставятся в `virtual_env/packages/` командами `ergoms setup` / `ergoms install-python` / `ergoms install-nodejs` (см. [`.docs/cli.md`](../../.docs/cli.md)).
- Python venv проекта — **`virtual_env/python/`** (создаётся из portable при setup).
- PostgreSQL 14+ — на компьютере или в Docker; параметры — в `.env` / `databases.yaml`.
- В пользовательской документации все команды выполняют через **`ergoms`**, а не через `python manage.py` напрямую.

## Кроссплатформенность

Любая команда ergoms должна работать на **Windows** (PowerShell) и **Linux** (bash). Если поведение зависит от ОС, в `core/deployment/commands.conf` используют префиксы **`win:`** и **`linux:`**; общая логика — в `core/deployment/windows/` и `core/deployment/linux/`.

Абстракции в коде приложения:

| Область | Где |
|---------|-----|
| Python (пути, процессы) | `core/api/src/core/utils/os_abstraction/` |
| Node (процессы) | `core/client/scripts/lib/process-ops.js` |

Подробнее — [`oc.mdc`](../../.cursor/rules/oc.mdc).

## Файл commands.conf

`core/deployment/commands.conf` — реестр команд ядра: строки вида `имя-команды=тип:действие`. Модули добавляют свои команды в `modules/<имя>/ergoms.conf`.

Часть команд **не** в `commands.conf`, а встроена в `ergo_ms.ps1` / `ergo_ms.sh`: **install-cli**, **help**, маршрутизация. Составные сценарии маршрутизируются префиксом **`lifecycle:`** в `commands.conf` (вызов `Invoke-LifecycleRunner` / `invoke_lifecycle_runner`) или встроены в shell-обёртки. Примитивы (`api:`, `npm:`) остаются в `commands.conf`.

## Единый lifecycle-pipeline

Любой составной сценарий установки, развёртывания и эксплуатации:

`ergoms` / `tasks.json` / `ergo_ms.ps1|sh` / `commands.conf` → [`lifecycle/runner.py`](lifecycle/runner.py) → [`DeploymentOrchestrator`](lifecycle/orchestrator.py) → [`DeploymentPipeline`](lifecycle/pipeline.py) → шаги [`DeploymentStep`](lifecycle/steps/base.py).

| Слой | Назначение |
|------|------------|
| [`lifecycle/recipes.py`](lifecycle/recipes.py) | Реестр рецептов: `setup-full`, `install-deps`, `deploy-*`, `service-*`, `nginx-*`, `docker-*`, `dev-*` |
| [`lifecycle/context.py`](lifecycle/context.py) | `runtime` (host/docker), `target`, `options` (sudo, purge, worker_key, …) |
| [`lifecycle/host/ops.py`](lifecycle/host/ops.py) | venv, `run_api_command`, npm, foreground-скрипты |
| [`lifecycle/host/privilege.py`](lifecycle/host/privilege.py) | Linux: re-exec через `sudo` для infra-рецептов |
| [`lifecycle/steps/`](lifecycle/steps/) | Общие (`common_steps`), host, service, infra, compose, dev |
| [`lifecycle/services/`](lifecycle/services/) | Каталог служб; Python backends (`backends/nginx_backend.py`, `redis_backend.py`) + `internal_dispatch` → shell для install/NSSM/systemd |
| [`docker_cli.py`](docker/docker_cli.py) | Тонкий argparse → `run_recipe('docker-*')` |

Низкоуровневые примитивы (`api:migrate`, `npm:run build`, прямой вызов `start_*.py`) остаются в `commands.conf` для разработки; **составные** цепочки — только через runner.

Список рецептов: `python core/deployment/lifecycle/runner.py --list` (или `py -3.12 …` на Windows).

### Задачи VS Code / Cursor

В [`.vscode/tasks.json`](../../.vscode/tasks.json) — **только** `ergoms <команда>`. Прямой вызов `ergo_ms.ps1`, `sudo bash ergo_ms.sh` или `docker_cli.py` из задач **запрещён**. Пример: **Setup Full System** → `ergoms setup && ergoms install-extensions`.

### Кодировка PowerShell (Windows)

Файлы `.ps1` в `core/deployment/` с кириллицей — **UTF-8 с BOM** (иначе PowerShell 5.1 ломает разбор). Запись из Python — [`ps1_io.write_ps1()`](scripts/ps1_io.py). Проверка: `ergoms ps1-encoding-check`; исправление: `ergoms ps1-encoding-check --fix` (входит в `core-rules-check`). Подробнее — [`.docs/lifecycle-pipeline.md`](../../.docs/lifecycle-pipeline.md), [`.cursor/rules/ps1-encoding.mdc`](../../.cursor/rules/ps1-encoding.mdc).

Команды **разнесены по отдельным файлам** — не один монолитный скрипт на всё, а шаги, которые можно переиспользовать и тестировать.

## Каталог wrappers

`core/deployment/wrappers/` — временные и генерируемые скрипты, которые создаются в процессе работы команд. Не храните здесь исходники «навсегда»; содержимое можно пересоздать.

## GeoIP (DB-IP City Lite)

Локальная геолокация IP для сессий пользователя и журнала аудита. Поиск только по файлу на диске — IP **не** отправляется во внешние API.

| Что | Где |
|-----|-----|
| База MMDB | `virtual_env/resources/geoip/dbip-city-lite.mmdb` (не в git) |
| Настройки | `.env`: `GEOIP_ENABLED`, `GEOIP_DOWNLOAD_URL` (см. `.env.example`) |
| Код | `core/api/src/core/utils/geoip.py`, `core/api/src/config/settings/geoip.py` |

### Команды

- `ergoms geoip-download` — скачать или обновить MMDB с db-ip.com (URL из `.env` или авто по месяцу)
- `ergoms geoip-backfill` — заполнить city/country у существующих `UserDevice` (`--dry-run` для проверки без записи)

### Первичная настройка GeoIP

1. Установите Python-зависимости: `ergoms python-install`
2. Скачайте базу: `ergoms geoip-download`
3. При необходимости заполните старые записи: `ergoms geoip-backfill`

### Обновление базы

Раз в месяц выполните `ergoms geoip-download`, затем перезапустите API — reader кэшируется в процессе.

## Redis (optional, portable packages)

Опциональный локальный Redis для общего кэша Django и channel layer. В `setup-full` ставится при `ERGO_BROKER=redis` в `.env`.

| Что | Где |
|-----|-----|
| Бинарники | `virtual_env/packages/redis/` (Windows: zip redis-windows 7.4.x msys2; Linux: сборка из tarball 7.4.x) |
| Конфиг | `virtual_env/packages/redis/conf/redis.conf` |
| Скрипты | `core/deployment/scripts/install_redis.py`, `resolve_env.py` |
| Windows | `core/deployment/windows/lib/redis.ps1`, служба `ergo_ms_redis` (NSSM) |
| Linux | `core/deployment/linux/lib/redis.sh`, unit `ergo_ms_redis.service` |

### Команды

- `ergoms install-redis [port]` — установка и запуск (как `install-nginx`: бинарники, конфиг, старт процесса)
- `ergoms install-redis-service` — автозапуск (Windows service / systemd)
- `ergoms start-redis` / `stop-redis` / `restart-redis` / `status-redis` / `test-redis`
- `ergoms uninstall-redis` / `uninstall-redis --purge` (Linux) / `-Purge` (Windows)

### Первичная настройка Redis

1. В `.env`: `ERGO_BROKER=redis`, затем `ergoms install-redis` или `setup-full` (шаг `EnsureRedisStep`). Секция `redis` в `databases.yaml` появится сама, если её ещё не было.
2. Перезапустить API
3. Проверка: `ergoms test-redis` → `PONG`

Режим — `ERGO_BROKER`; host/port/db — `databases.yaml` → `redis`. Effective-логика — [`redis_runtime.py`](../../core/api/src/config/redis_runtime.py).

## Nginx (optional, portable packages)

Обратный прокси для запуска как на сервере: один origin для клиента, API, WebSocket и media_api. В `setup-full` ставится при `ERGO_PROXY=nginx` в `.env`.

| Что | Где |
|-----|-----|
| Бинарники | `virtual_env/packages/nginx/` |
| Шаблон конфига | `core/deployment/nginx/ergo_ms.conf.template` |
| Рендер конфига | `core/deployment/scripts/render_nginx_config.py`, `resolve_env.py` |
| Effective-переменные | `nginx_runtime.py`, `env_resolvers.py`, `ergo_modes.py` |
| Windows | `core/deployment/windows/lib/nginx.ps1`, служба `ergo_ms_nginx` (NSSM) |
| Linux | `core/deployment/linux/lib/nginx.sh`, unit `ergo_ms_nginx.service` |
| Эталон фрагмента | `env/nginx.env.example` |

### Команды

- `ergoms install-nginx` — установка бинарников, рендер конфига, запуск
- `ergoms install-nginx-service` — автозапуск (Windows / systemd)
- `ergoms reload-nginx` — проверка конфига и перезагрузка
- `ergoms start-nginx` / `stop-nginx` / `restart-nginx` / `status-nginx` / `test-nginx`
- `ergoms uninstall-nginx` / `uninstall-nginx --purge` (Linux) / `-Purge` (Windows)

Команды nginx/redis/TLS и служб делегируют в [`lifecycle/runner.py`](../../core/deployment/lifecycle/runner.py) (рецепты `nginx-*`, `redis-*`, `tls-*`, `service-*`). На Linux `sudo` выполняется внутри runner (`privilege.py`), не через `sudo bash ergo_ms.sh` в VS Code tasks.

### Первичная настройка nginx

1. В корневом `.env`: `ERGO_PROXY=nginx`; детали — `env/nginx.env` из `env/nginx.env.example`.
2. `ergoms install-nginx` (или `setup-full` при уже `ERGO_PROXY=nginx`; на Linux runner при необходимости запросит `sudo`).
3. Проверка: `ergoms test-nginx`, `ergoms status-nginx`.
4. После смены клиента: `ergoms client-build` (при `ERGO_PROXY=nginx` команда сама пересобирает сайт-конфиг и делает reload).

Переменные: `ERGO_PROXY`, `NGINX_*` в `env/nginx.env`.

## TLS (Let's Encrypt, Linux)

Выпуск и обновление сертификатов для nginx. **Только Linux** (certbot, root/sudo).

| Что | Где |
|-----|-----|
| Скрипты | `core/deployment/linux/lib/tls.sh`, `core/deployment/scripts/tls_cli.py` |
| Конфиг доменов | `core/deployment/nginx/tls_config.py`, `tls_runtime.py` |
| Hook после renew | `core/deployment/nginx/hooks/certbot-deploy-reload-nginx.sh` |

### Команды

- `sudo ergoms install-tls` — certbot, выпуск сертификата, HTTPS в nginx
- `sudo ergoms renew-tls` — обновление (`certbot renew`)
- `ergoms status-tls` — срок действия и пути (без root)

### Первичная настройка TLS

1. nginx установлен и `ERGO_PROXY=nginx`.
2. В `env/nginx.env`: `ERGO_TLS_EMAIL`, домены (`NGINX_SERVER_NAME` или `ERGO_TLS_DOMAINS`).
3. `sudo ergoms install-tls`.
4. Сверьте `ergoms status-tls` с путями `ERGO_SSL_CERT` / `ERGO_SSL_KEY` в `env/nginx.env`.

TLS: `ERGO_TLS_*`, `ERGO_SSL_CERT`, `ERGO_SSL_KEY` — `env/nginx.env`. CORS: `CORS_ALLOWED_ORIGINS` — корневой `.env`.

## Docker Compose

Запуск стека в контейнерах — альтернатива portable Redis/nginx на хосте и отдельным процессам `ergoms dev`. Команды — префикс **`ergoms docker-*`** в `commands.conf`; CLI [`docker_cli.py`](docker/docker_cli.py) делегирует compose-операции в рецепты `docker-up`, `docker-down`, `docker-build`, … через [`DeploymentOrchestrator.run_recipe`](lifecycle/orchestrator.py).

| Что | Где |
|-----|-----|
| Compose-файлы | `core/deployment/docker/docker-compose*.yml` |
| Образы | `Dockerfile.python`, `Dockerfile.client` |
| Effective env (read-only) | [`docker_runtime.py`](docker/docker_runtime.py) |
| Entrypoint | `entrypoint/docker_entrypoint.sh`, `wait_for_services.py` |
| Worker-сервисы | `generate_workers_compose.py` → `docker-compose.workers.generated.yml` |
| nginx в Docker | `nginx/ergo_ms.docker.conf.template` → `ergo_ms.conf.rendered` |

### Команды

- `ergoms docker-init` — build, gen-workers, up, install, migrate
- `ergoms docker-clean` — down с томами, локальные образы, артефакты compose (подтверждение в терминале; `--yes` — без вопроса)
- `ergoms docker-up` / `docker-down` / `docker-restart`
- `ergoms docker-dev` / `docker-prod` — режим из `--mode` или `DOCKER_MODE` в `.env`
- `ergoms docker-build`, `docker-ps`, `docker-logs`, `docker-migrate`, `docker-install-deps`, `docker-install-npm`, `docker-shell-api`, `docker-gen-workers`

Описания для `ergoms help` — раздел `docker` в [`help.manifest.yaml`](help.manifest.yaml).

### Первичная настройка Docker

1. Docker Desktop / Docker Engine + Compose V2
2. В `.env`: `DOCKER_ENABLED=true`, `DOCKER_MODE`, profiles (`DOCKER_PROFILE_*`)
3. `databases.yaml` — при `DOCKER_DATABASE=container` хост `localhost` подменяется на сервис `postgres`
4. `ergoms docker-init`

Переменные: секция **Docker** в `.env.example`. Порты — `API_PORT`, `CLIENT_PORT` и др. (не отдельные `DOCKER_*_PORT`).

### Генерируемые артефакты (не в git)

- `.compose.env`, `.compose.databases.yaml`
- `docker-compose.workers.generated.yml`
- `docker-compose.publish.generated.yml`
- `init/postgres/02-celery-databases.sql`
- `nginx/ergo_ms.conf.rendered`

Скрипты **не пишут** в корневой `.env` / `databases.yaml` — только артефакты в `core/deployment/docker/`. Подробнее — [`.docs/docker.md`](../../.docs/docker.md), [`.cursor/rules/docker.mdc`](../../.cursor/rules/docker.mdc).

## Типичные ошибки

| Симптом | Что проверить |
|---------|----------------|
| `ergoms` не найден | Работайте из корня проекта; `core/deployment/bin` в PATH (Project-Shell). На Linux `install-cli` ставит `/usr/local/bin/ergoms` для `sudo ergoms`. `uninstall-cli` снимает только эту ссылку, файлы в bin не удаляет |
| Команда есть только на одной ОС | Префикс `win:` / `linux:` в `commands.conf` |
| Окружение повреждено после ручного venv | Не создавай `.venv` — только `virtual_env/python/` ([`virtual-env.mdc`](../../.cursor/rules/virtual-env.mdc)) |
| ParserError в `.ps1`, кракозябры вместо кириллицы | UTF-8 без BOM — `ergoms ps1-encoding-check --fix` |

## См. также

| Тема | Файл |
|------|------|
| Lifecycle-pipeline (для людей) | [`.docs/lifecycle-pipeline.md`](../../.docs/lifecycle-pipeline.md) |
| Lifecycle-pipeline (правило агента) | [`.cursor/rules/lifecycle-pipeline.mdc`](../../.cursor/rules/lifecycle-pipeline.mdc) |
| UTF-8 BOM в .ps1 | [`.cursor/rules/ps1-encoding.mdc`](../../.cursor/rules/ps1-encoding.mdc) |
| Справочник команд ergoms | [`.docs/cli.md`](../../.docs/cli.md) |
| Только ergoms, не manage.py | [`.cursor/rules/no-direct-manage-py.mdc`](../../.cursor/rules/no-direct-manage-py.mdc) |
| Службы Linux / Windows | [`.docs/deployment.md`](../../.docs/deployment.md) |
| Redis, nginx, TLS (правила агента) | [`.cursor/rules/deployment-infra.mdc`](../../.cursor/rules/deployment-infra.mdc) |
| Docker Compose (правила агента) | [`.cursor/rules/docker.mdc`](../../.cursor/rules/docker.mdc) |
| Docker (документация) | [`.docs/docker.md`](../../.docs/docker.md) |
