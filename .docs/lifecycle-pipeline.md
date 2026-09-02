# Единый pipeline развёртывания (lifecycle)

ERGO MS выполняет **составные** сценарии установки, развёртывания и эксплуатации через один механизм — **lifecycle-pipeline**. Пользователь по-прежнему вызывает **`ergoms`** или задачи VS Code; внутри команда превращается в цепочку шагов с общим контекстом и единым кодом возврата.

Техническая документация для разработчиков deployment — [`core/deployment/logic.md`](../core/deployment/logic.md). Правила для агента Cursor — `.cursor/rules/lifecycle-pipeline.mdc`.

## Зачем это нужно

Раньше одни и те же действия дублировались в `commands.conf`, `setup.ps1` / `setup.sh`, встроенных обработчиках `ergo_ms.ps1` / `ergo_ms.sh` и частично в Docker CLI. Теперь:

- **один** реестр рецептов (`setup-full`, `install-deps`, службы, nginx/redis, Docker, dev);
- **одни** шаги для хоста и Docker, где это возможно (Python, npm, миграции, прогрев кэшей);
- **одинаковые** имена рецептов на Windows и Linux.

Низкоуровневые примитивы для разработки (`ergoms api migrate`, `ergoms npm run build`) остаются в `commands.conf` без pipeline.

## Как это выглядит для вас

Вы ничего не меняете в привычных командах:

```cmd
ergoms setup
ergoms install-deps
ergoms dev
ergoms start-client
ergoms install-nginx
ergoms start
ergoms docker-up
```

Задачи VS Code (**Setup Full System**, **Start All Services**, nginx/redis) тоже вызывают только **`ergoms …`**, без прямого запуска `ergo_ms.ps1` или `sudo bash ergo_ms.sh`.

На Linux команды установки nginx, Redis, TLS и системных служб, которым нужны права root, запрашивают **`sudo` внутри pipeline** — не нужно оборачивать задачу редактора в `sudo bash …`.

## Цепочка выполнения

```
ergoms / задача VS Code / ergo_ms.ps1|sh
    → core/deployment/lifecycle/runner.py <имя_рецепта>
    → DeploymentOrchestrator
    → последовательность шагов (DeploymentStep)
```

Список всех рецептов (для отладки):

```cmd
py -3.12 core/deployment/lifecycle/runner.py --list
```

На Windows, если `py` в PATH:

```cmd
py -3.12 core\deployment\lifecycle\runner.py --list
```

Колонки: имя рецепта, группа (`deployment`, `service`, `infra` — nginx/Redis/TLS, `compose`, `foreground`), краткое описание.

## Группы рецептов

| Группа | Что покрывает | Примеры ergoms |
|--------|---------------|----------------|
| Установка и deploy | venv, зависимости, миграции, сборка | `setup`, `install-deps`, `deploy-all`, `build-all` |
| Службы ОС | install/uninstall/start/stop/status через NSSM или systemd; модульные службы — из `host_lifecycle.yaml`; portable Postgres — при `ERGO_DB=portable_postgres` | `install-services`, `uninstall-services`, `start`, `stop`, `status` |
| Инфраструктура (`infra`) | portable nginx, Redis, TLS (Linux) — обратный прокси, кэш/брокер, сертификаты | `install-nginx`, `start-redis`, `install-tls` |
| Docker Compose | up/down/build/migrate в контейнерах | `docker-init`, `docker-up`, `docker-migrate` |
| Разработка (foreground) | процессы в терминале | `dev`, `start-client`, `start-worker`, `warmup-caches-if-needed` |

Имена в `ergoms help` совпадают с именами рецептов или их **алиасами** (например `setup` → рецепт `setup-full`).

## Первичная настройка и задачи редактора

| Задача VS Code | Команда |
|----------------|---------|
| Setup Full System | `ergoms setup && npm run install-extensions` |
| Start All Services | `ergoms start-all` (с прогревом кэшей) |
| Nginx / Redis | `ergoms install-nginx`, `ergoms install-redis`, … |

Альтернатива без редактора — из корня проекта:

```cmd
ergoms setup
```

После первичной настройки команда `ergoms` доступна из корня проекта и подпапок (`core/deployment/bin`; в IDE — профиль Project-Shell).

## Docker

Команды `ergoms docker-*` и скрипт `docker_cli.py` делегируют compose-операции в те же рецепты, что и хостовый pipeline (`docker-up`, `docker-down`, `docker-build`, …). Подробнее — [docker.md](docker.md).

## PowerShell и кириллица (Windows)

Скрипты `.ps1` в `core/deployment/` с русским текстом должны быть сохранены в **UTF-8 с BOM**. Иначе Windows PowerShell 5.1 может не разобрать файл (ошибки парсинга с «кракозябрами» вместо кириллицы).

Проверка и исправление:

```cmd
ergoms ps1-encoding-check
ergoms ps1-encoding-check --fix
```

Проверка также входит в `ergoms core-rules-check`. Подробнее — [troubleshooting.md](troubleshooting.md#ошибка-парсинга-powershell-ps1).

## Для разработчиков: добавить новую составную команду

1. Реализуйте шаг в `core/deployment/lifecycle/steps/` (или обёртку над существующим скриптом).
2. Зарегистрируйте рецепт в `core/deployment/lifecycle/recipes.py`.
3. Добавьте строку в `core/deployment/commands.conf` → вызов `runner.py <recipe>` для `win:` и `linux:`.
4. Добавьте `summary` в `core/deployment/help.manifest.yaml`.
5. Если меняется запуск процесса — сверьте хост и Docker ([docker.md](docker.md)).

Не добавляйте длинные цепочки `api:… && npm:…` в `commands.conf` для сценариев установки — используйте один рецепт.

## См. также

| Тема | Документ |
|------|----------|
| Справочник команд | [cli.md](cli.md) |
| Устройство deployment | [logic.md](../core/deployment/logic.md) |
| Системные службы | [deployment.md](deployment.md) |
| Docker | [docker.md](docker.md) |
| Типичные ошибки | [troubleshooting.md](troubleshooting.md) |
