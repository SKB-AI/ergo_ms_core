# Управление через ergoms

Команды Django, npm и Poetry в пользовательской документации не вызывают напрямую. Единая точка входа — утилита **`ergoms`**: она читает команды из `core/deployment/commands.conf` и из `modules/*/ergoms.conf`, сама выбирает нужный каталог и виртуальное окружение.

Технический формат команд (префиксы `api:`, `npm:`, `win:` и т.д.) описан в `.cursor/rules/ergoms-commands.mdc`. Ниже — то, что нужно при локальной разработке и обслуживании проекта.

## Единый lifecycle-pipeline

Составные команды (`setup`, `install-deps`, `deploy-*`, службы, nginx/redis, `docker-*`, `dev`, `start-*`) выполняются через [`core/deployment/lifecycle/runner.py`](../core/deployment/lifecycle/runner.py): рецепт → цепочка шагов. Для вас это прозрачно — вызывайте **`ergoms`** как раньше.

Подробнее: [lifecycle-pipeline.md](lifecycle-pipeline.md). Список рецептов: `py -3.12 core/deployment/lifecycle/runner.py --list`.

Проверка кодировки `.ps1` на Windows: `ergoms ps1-encoding-check` (см. [troubleshooting.md](troubleshooting.md#ошибка-парсинга-powershell-ps1)).

## Локальная разработка

Для запуска API с прогревом кэшей выполните:

```cmd
ergoms dev
```

Клиент Vue запускается отдельной командой:

```cmd
ergoms start-client
```

Файловый сервис, исполнитель Celery и планировщик beat — по отдельности:

```cmd
ergoms start-media
ergoms start-worker
ergoms start-beat
```

Полный набор для разработки (API, клиент, Media API, worker, beat) одной командой:

```cmd
ergoms start-all
```

На Windows каждый сервис откроется в отдельном окне терминала. В Cursor и VS Code тот же набор — **`Ctrl+Shift+B`** (задача **`Start All Services`**).

## База данных и статика

Работа со схемой БД и подготовка артефактов для развёртывания:

```cmd
ergoms db-makemigrations
ergoms db-migrate
ergoms migrate-all
ergoms collectstatic
ergoms client-build
ergoms client-check
ergoms build-all
```

Первые три команды создают и применяют миграции; `client-build` собирает клиент, `client-check` — полный прогон lint/i18n/build/a11y с логами в `logs/client-check/`; `build-all` собирает клиент и статику Django.

## Зависимости и первичная настройка {#зависимости-и-первичная-настройка}

При первом клонировании репозитория файлы **`core/deployment/bin/ergoms.cmd`** / **`ergoms`** уже на месте. Команда работает только из каталога проекта и его подпапок. Полная первичная настройка (`setup-full`) — по инструкции в [README.md](../README.md). После неё доступны, в частности:

```cmd
ergoms setup
ergoms install-deps
ergoms python-install
ergoms npm run install:all
ergoms warmup-caches
```

Команда `install-deps` обновляет зависимости Python и npm, применяет миграции и прогревает кэши — когда окружение уже есть, но его нужно освежить после pull или смены ветки.

## Любая команда Django

Команды Django вызываются через прокси `ergoms api`:

```cmd
ergoms api createsuperuser
ergoms api shell
ergoms api <имя_команды> [аргументы]
```

Так же вызываются модульные команды, например `ergoms api init_technologies` или `ergoms api seed_lms_demo`.

## Команды модулей

Модуль может добавить собственные имена команд в `modules/<имя>/ergoms.conf`. Тогда они вызываются с префиксом модуля:

```cmd
ergoms video_analysis:install
```

Справка по модулям (описания хранятся в `modules/<имя>/ergoms.help.yaml`):

```cmd
ergoms help modules
ergoms help module video_analysis
```

Общая справка по ядру: `ergoms help`.

## Зависимости модулей (Python)

Пакеты для модуля прописывают не в корневом `pyproject.toml`, а в `modules/<имя>/pyproject.toml`. Управляют этим через прокси `ergoms api`:

```cmd
ergoms api module-add <модуль> <пакет>
ergoms api module-add <модуль> <пакет> ">=1.0.0"
ergoms api module-add <модуль> <пакет> --install
ergoms api module-remove <модуль> <пакет>
ergoms api module-list
```

`module-add` без явной версии сам подбирает последнюю совместимую; флаг `--install` сразу устанавливает пакет. После добавления или удаления без `--install` нужно выполнить `ergoms python-install`, чтобы применить изменения (установка недостающих и удаление пакетов, которых больше нет в `pyproject.toml` / `poetry.lock`).

Модульные пакеты **не** добавляют в корневой `pyproject.toml` и **не** должны попадать в `poetry.lock`. Пересборка lock ядра — только при изменении зависимостей в корневом `pyproject.toml` (`ergoms poetry lock`).

Обновление в пределах ограничений версий — ядро и модули:

```cmd
ergoms python-update
ergoms npm update
ergoms update-all
```

`python-update` / `poetry update` обновляет `poetry.lock` ядра и пакеты из `modules/*/pyproject.toml`; `npm update` — зависимости npm-root и `modules/*/client/package.json`.

## Lock-файлы (ядро и модули)

`poetry.lock` в корне и `package-lock.json` в `virtual_env/npm/` — **только ядро**. Workspaces `../../modules/*/client` остаются в `virtual_env/npm/package.json`, но установка не должна записывать модули в lock.

```cmd
ergoms npm run install:all
ergoms npm-lock-refresh
ergoms npm-lock-sanitize
ergoms lock-check
```

- Установка npm (ядро + модули в `virtual_env/npm/node_modules`): `ergoms npm run install:all` — ставит недостающее, удаляет пакеты вне `package.json` и чистит `virtual_env/cache/npm` от неиспользуемых tarball'ов; не используйте `npm ci` в корне репозитория.
- После изменения зависимостей **ядра** в `virtual_env/npm/package.json`: `ergoms npm-lock-refresh`.
- Если в `package-lock.json` снова появились `modules/*` (например после старого `npm install`): `ergoms npm-lock-sanitize` или полная пересборка через `ergoms npm-lock-refresh`.
- Проверка утечек модулей в lock: `ergoms lock-check`.

В `virtual_env/npm/.npmrc` задано `package-lock=false`, чтобы обычный `npm install` не перезаписывал lock; пересборка lock — только через `ergoms npm-lock-refresh`.

Версии модульных npm-пакетов фиксируются в `modules/<имя>/client/package.json` submodule.

## GeoIP (геолокация IP)

Локальная база DB-IP City Lite для city/country в сессиях и журнале аудита:

```cmd
ergoms geoip-download
ergoms geoip-backfill
ergoms geoip-backfill --dry-run
```

Перед первым использованием включите **`GEOIP_ENABLED=true`** в `.env` и скачайте MMDB. Подробнее — [`core/deployment/logic.md`](../core/deployment/logic.md#geoip-db-ip-city-lite) и [configuration.md](configuration.md#geoip-геолокация-ip).

## Nginx (опционально) {#nginx-опционально}

Команды реализованы в `ergo_ms.ps1` / `ergo_ms.sh` (не в `commands.conf`). При `NGINX_ENABLED=true` ставит `setup-full`. Установка может потребовать прав администратора; `status-nginx` и `test-nginx` — без них.

```cmd
ergoms install-nginx
ergoms reload-nginx
ergoms test-nginx
ergoms status-nginx
ergoms start-nginx
ergoms stop-nginx
```

Служба с автозапуском: `ergoms install-nginx-service` (Windows). Эталон — [`env/nginx.env.example`](../env/nginx.env.example) при `ERGO_PROXY=nginx`.

## Redis (опционально) {#redis-опционально}

Portable-сборка в `virtual_env/packages/redis/`. При `ERGO_BROKER=redis` ставит `setup-full`.

```cmd
ergoms install-redis
ergoms test-redis
ergoms status-redis
ergoms start-redis
ergoms stop-redis
```

В `.env`: `ERGO_BROKER=redis`, перезапустите API. Служба: `ergoms install-redis-service`.

## Docker Compose {#docker-compose}

Запуск API, клиента, Redis, PostgreSQL, Celery и опционально nginx/Jupyter в контейнерах. Команды в `commands.conf`; режим `ERGO_RUNTIME=docker`, детали — `env/docker.env`, БД — `databases.yaml`.

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

- **`docker-init`** — первый запуск: сборка образов, worker-сервисы, `up`, миграции
- **`docker-clean --yes`** — полная очистка: контейнеры, тома (включая PostgreSQL), локальные образы, сгенерированные файлы compose
- **`docker-dev`** / **`docker-prod`** — `docker-up` с `DOCKER_MODE=dev` или `prod`
- **`docker-migrate`** — миграции и прогрев кэшей внутри контейнера `api`
- **`docker-install-deps`** — Python-зависимости (poetry) внутри контейнера `api`
- **`docker-install-npm`** — npm-зависимости внутри стека Docker
- **`docker-gen-workers`** — пересоздать compose-фрагмент из `celery_workers.yaml`

В `.env` задайте `DOCKER_ENABLED=true`, режим `DOCKER_MODE` и при необходимости profiles (`DOCKER_PROFILE_POSTGRES`, `DOCKER_PROFILE_NGINX`, `DOCKER_PROFILE_JUPYTER`). Подробнее — [docker.md](docker.md) и [`core/deployment/logic.md`](../core/deployment/logic.md#docker-compose).

Не смешивайте Docker-стек с portable Redis/nginx на хосте на тех же портах.

## TLS (Let's Encrypt, Linux)

```bash
sudo ergoms install-tls
ergoms status-tls
sudo ergoms renew-tls
```

## Обслуживание и развёртывание

```cmd
ergoms restore-menu
ergoms maintenance-on
ergoms maintenance-off
ergoms maintenance-status
ergoms rotate-logs
ergoms install-infra-log-rotate
ergoms invalidate-caches-warmup
ergoms deploy-api
ergoms deploy-client
ergoms deploy-all
```

`restore-menu` — восстановление пунктов бокового меню из миграций. `maintenance-on/off` — режим технических работ без перезапуска служб; `maintenance-status` — текущее состояние. `rotate-logs` — ротация журналов nginx, Redis и client-dev; `install-infra-log-rotate` — ежедневный планировщик ротации. `invalidate-caches-warmup` — сброс кэшей ядра и прогрев.

## Системные службы (Linux / Windows)

Чтобы зарегистрировать API, клиент, Celery и media_api как службы операционной системы, нужны права администратора:

```cmd
ergoms start
ergoms stop
ergoms restart
ergoms status
ergoms logs ergo_ms_api_dev
```

Подробнее — в [deployment.md](deployment.md) (Linux systemd и Windows services). Для обычной разработки службы не нужны: достаточно `ergoms dev` и `ergoms start-client`.

## Как устроен конфиг команд

В `core/deployment/commands.conf` строки вида `имя-команды=тип:действие`. Несколько шагов объединяют через `&&`. Например:

```conf
migrate-all=api:makemigrations && api:migrate
install-deps=api:install && npm:run install:all && api:migrate && api:warmup_caches
```

Команды `dev` и `start-client` вызывают скрипты в `core/api/scripts/` и `core/deployment/scripts/` (см. `commands.conf`). Команды **nginx**, **redis** и **TLS** — встроенные в `ergo_ms.ps1` / `ergo_ms.sh`; полный список — `ergoms help`.

Новую команду ядра добавляют в `commands.conf` и описание — в `core/deployment/help.manifest.yaml`. Команду модуля — в `ergoms.conf` и `ergoms.help.yaml` соответствующего модуля.

## Остановка

Процессы, запущенные вручную в терминале, останавливают сочетанием **`Ctrl+C`**. Системные службы — командой `ergoms stop`.

## См. также

| Вопрос | Документ |
|--------|----------|
| Как устроена система в целом | [architecture.md](architecture.md) |
| Возможности модулей (hook’и, службы, packages) | [modules.md](modules.md) |
| Запуск для разработки, логи | [development.md](development.md) |
| Настройка `.env` и баз данных | [configuration.md](configuration.md) |
| Если команда завершилась с ошибкой | [troubleshooting.md](troubleshooting.md) |
| Docker Compose | [docker.md](docker.md) |
