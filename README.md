# ERGO MS

ERGO MS — модульный фреймворк для корпоративных веб-приложений. Ядро даёт общую инфраструктуру (аутентификация и профиль, роли и права, меню и оболочка клиента, управление контентом, уведомления, журнал действий, файлы, мост между модулями, фоновые задачи, утилита ergoms для управления системой), а расширять логику работы системы можно с помощью модулей в папке `modules/`.

Стек: Django 5 и DRF на сервере, Vue 3 и Vite на клиенте, PostgreSQL (или SQLite/MySQL) как основная БД, Celery (worker и beat) для фоновых, отложенных и периодических вычислительных операций, Poetry и npm. Опционально — portable **Redis** (кэш, channel layer, брокер Celery) и **nginx** (запуск как на сервере).

## Документация

- [Архитектура](.docs/architecture.md) — ядро, модули, интеграции
- [Вынос модулей в сервисы](.docs/modularization.md) — уровни 1–3, ADR, пилот, чеклист
- [Возможности модулей](.docs/modules.md) — каталог hook’ов и точек расширения без правок ядра
- [Структура проекта](.docs/structure.md) — каталоги и конфигурация
- [Настройка .env и БД](.docs/configuration.md) — первичная конфигурация
- [Режимы безопасности](.docs/security-modes.md) — уровни ERGO_SECURITY, каталог контролей, требования модулей
- [Аудит безопасности ядра](.docs/security-audit.md) — состояние ядра и перечень отклонений
- [Разработка](.docs/development.md) — запуск для разработки и логи
- [Команды ergoms](.docs/cli.md) — справочник команд
- [Lifecycle-pipeline](.docs/lifecycle-pipeline.md) — единая цепочка setup, deploy, служб, Docker и dev
- [Проблемы при установке](.docs/troubleshooting.md) — типичные ошибки
- [Системные службы](.docs/deployment.md) — systemd / Windows services, запуск как на сервере
- [Docker Compose](.docs/docker.md) — запуск стека в контейнерах (`ergoms docker-*`)
- [Развёртывание ergoms](core/deployment/logic.md) — commands.conf, GeoIP, Redis, nginx, TLS, Docker, realtime за reverse proxy
- [Ядро API](core/api/README.md) · [Клиент](core/client/README.md) · [Media API](core/media_api/README.md)

## Быстрый старт

Путь к проекту на диске должен содержать только латиницу, цифры, дефис и подчёркивание — без кириллицы и пробелов. Например: `C:\projects\ergo_ms\`.

Заранее нужен **Git**. Python 3.12, Node.js LTS и PostgreSQL при `setup-full` по умолчанию ставятся как portable в `virtual_env/packages/` — в шаблоне [`.env.example`](.env.example) уже `PORTABLE_PYTHON_ENABLED=true`, `PORTABLE_NODEJS_ENABLED=true` и `ERGO_DB=portable_postgres`. Portable Postgres слушает порт **5433** (параметры — в [`databases.yaml`](databases.yaml.example)). Если нужны уже установленные на компьютере Python, Node.js или PostgreSQL — см. [ниже](#системные-python-nodejs-и-postgresql).

Чтобы склонировать репозиторий, выполните в терминале:

```cmd
git clone https://github.com/SKB-AI/ergo_ms_core ergo_ms
cd ergo_ms
```

Стандарт среды разработки — **VS Code**, **Cursor** и совместимые с ними редакторы (общая экосистема расширений и задач `.vscode/`). Откройте каталог **`ergo_ms`** в одном из них — в проекте уже настроены задачи (`.vscode/tasks.json`), через них проще всего установить окружение и запустить сервисы.

### 1. Первая установка (задача)

1. Меню **Terminal → Run Task…** (или **Терминал → Выполнить задачу…**).
2. Выберите **`Setup Full System`**.

Задача выполнит полную настройку (`setup-full`): виртуальное окружение, зависимости Python и npm, проверку локальной утилиты `ergoms` в корне проекта, расширения редактора. На Windows политика выполнения скриптов обходится автоматически. На Linux для команд установки nginx и системных служб runner при необходимости запросит `sudo`.

После установки появятся файлы `.env`, `databases.yaml` и `celery_workers.yaml` (из примеров, если их ещё не было). Проверьте их и при необходимости отредактируйте под свою среду — см. [configuration.md](.docs/configuration.md). Если параметры БД отличались от примера и миграции не применились, поправьте `databases.yaml` и снова выполните задачу **`Setup Full System`** или в терминале: `ergoms migrate-all`.

Подробнее о командах установки — [cli.md](.docs/cli.md#зависимости-и-первичная-настройка).

**Альтернатива без редактора** — из корня проекта в терминале:

```cmd
ergoms setup
```

Команда `ergoms` — файлы в **`core/deployment/bin/`** (`ergoms.cmd` на Windows, `ergoms` на Linux). Она работает **только** если текущий каталог — корень проекта или его подпапка. В Cursor/VS Code профиль **Project-Shell** уже добавляет этот каталог в PATH. Если терминал ещё не видит `ergoms`, один раз выполните полную настройку через скрипт оболочки:

Windows (PowerShell):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\core\deployment\windows\ergo_ms.ps1 setup-full
```

Linux:

```bash
bash core/deployment/linux/ergo_ms.sh setup-full
```

На Linux для команд установки nginx и системных служб runner при необходимости запросит `sudo` сам — отдельно оборачивать в `sudo bash …` не нужно.

### 2. Запуск для разработки

После установки нажмите **`Ctrl+Shift+B`** — задача **`Start All Services`** запустит API, клиент, Celery worker и beat, media_api (с предварительным прогревом кэшей). Если в `.env` задано `ERGO_JUPYTER` отличное от `none`, JupyterLab откроется отдельным терминалом. Отдельные сервисы можно запустить через **Run Task…** — **`Client`**, **`API`**, **`Media API`** и т.д.

В терминале то же самое: **`ergoms start-all`** — прогрев кэшей и запуск тех же сервисов (на Windows каждый в отдельном окне). Дальнейшие операции обслуживания — через **`ergoms`**; обновить зависимости без полной переустановки: `ergoms install-deps`.

После запуска откройте в браузере **клиент** — это основная точка входа в систему:

**http://localhost:8001** — Vue-приложение: форма входа, боковое меню, страницы ядра и подключённых модулей. Клиент сам обращается к серверу по адресу из `.env` (`API_HOST`, `API_PORT`); отдельно открывать API для обычной работы не нужно. При первом запуске, если учётной записи ещё нет, создайте администратора в терминале: `ergoms api createsuperuser`, затем войдите на этой странице.

Порт можно изменить в `.env`, если он уже занят на вашем компьютере. Подробнее о сервисах и портах — в [development.md](.docs/development.md#адреса-в-браузере).

### 3. Опционально: Redis и nginx

Для локальной разработки с одним процессом API Redis не обязателен. Если нужен общий кэш, channel layer между несколькими worker API или брокер Celery на Redis:

```cmd
ergoms install-redis
```

Затем вручную в `.env`: `ERGO_BROKER=redis` (параметры — секция `redis` в `databases.yaml`), перезапустите API. Проверка: `ergoms test-redis` → `PONG`. Подробнее — [configuration.md](.docs/configuration.md#redis-и-несколько-процессов) и [`core/deployment/logic.md`](core/deployment/logic.md#redis-optional-portable-packages).

Запуск как на сервере (обратный прокси nginx: клиент и API открываются с одного адреса в браузере, без разных портов): `ERGO_PROXY=nginx` и [`env/nginx.env.example`](env/nginx.env.example), команды — `ergoms install-nginx`, см. [cli.md](.docs/cli.md#nginx-опционально).

## Системные Python, Node.js и PostgreSQL

Этот раздел нужен только если **не** используете portable из быстрого старта. Окружение проекта всё равно живёт в `virtual_env/` — не создавайте вручную `.venv` / `venv` и не вызывайте `pip` / `npm` мимо `ergoms`.

**Python и Node.js.** Заранее установите Python **3.12** и Node.js **18+**. В `.env` **до** `setup-full` выставьте `PORTABLE_PYTHON_ENABLED=false` и `PORTABLE_NODEJS_ENABLED=false` (или поправьте `.env` и повторите `setup-full`). Тогда venv и npm-workspace соберутся из системных интерпретаторов. Явные `ergoms install-python` и `ergoms install-nodejs` всегда ставят portable в `virtual_env/packages/`, независимо от этих флагов.

**PostgreSQL.** В `.env` задайте `ERGO_DB=postgres`, в [`databases.yaml`](databases.yaml.example) — `host` / `port` вашей установки (часто `127.0.0.1` и **5432**), имя БД, пользователь и пароль. Базу `ergo_ms` создайте сами, если её ещё нет. Host и port скрипты в `.env` не пишут — только в yaml. Управление portable Postgres без полной настройки: `ergoms install-postgres`, `start-postgres`, `status-postgres`, `test-postgres`, `uninstall-postgres` (`--purge` / `-Purge` удаляет и каталог `virtual_env/packages/postgres`). Не держите одновременно portable на хосте и контейнерный Postgres на одном порту — см. [docker.md](.docs/docker.md).
