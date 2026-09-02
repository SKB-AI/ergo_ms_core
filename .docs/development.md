# Разработка

В режиме разработки ERGO MS работает как несколько процессов, которые можно запускать по отдельности или одним действием из IDE. Для этого не нужны права администратора — в отличие от установки системных служб на Linux или Windows.

## Какие сервисы запускаются

**API** (Django) по умолчанию слушает порт **8000**. Запуск: `ergoms dev`. Сервер работает через **ASGI** (Django Channels) — это нужно для WebSocket (уведомления, presence, мессенджер). Режим realtime задаётся `ERGO_REALTIME` в корневом `.env` (`websocket`, `sse` или `http_polling`; legacy-override: `REALTIME_TRANSPORT`); детали интервалов — [`env/realtime.env.example`](../env/realtime.env.example). При `http_polling` обновления идут через REST без постоянного push-соединения.

Перед стартом команда прогревает кэши обнаруженных приложений и модулей, поэтому первый запуск после клонирования может занять больше времени; последующие будут быстрее.

**Клиент** (Vue + Vite) — порт **8001**, команда `ergoms start-client`. Это отдельный сервер разработки с горячей перезагрузкой компонентов.

**Media API** — порт **8003**, команда `ergoms start-media`. Нужен для загрузки и скачивания файлов через подписанные URL.

**Celery worker** (`ergoms start-worker`) выполняет фоновые задачи из очередей. **Celery beat** (`ergoms start-beat`) — планировщик по расписанию. Список очередей — в `celery_workers.yaml`; параметры брокера — в `databases.yaml` (секции `celery`, `celery_worker`, `celery_beat` должны указывать на один брокер).

Команда **`ergoms start-all`** поднимает сервисы разработки: логи БД, API, клиент (или nginx), Media API, worker и beat. Если в `.env` включены соответствующие режимы, отдельно открываются Redis (`ERGO_BROKER=redis`), Meilisearch (`ERGO_SEARCH_ENABLED=true`) и JupyterLab (`ERGO_JUPYTER` не `none`). На Windows каждый процесс открывается в отдельном окне терминала; на Linux все процессы стартуют в фоне в одном терминале (остановка — `Ctrl+C`).

Тот же набор в Cursor или VS Code — **`Ctrl+Shift+B`**: задача **`Start All Services`** с предварительным прогревом кэшей. JupyterLab при включённом `ERGO_JUPYTER` появляется отдельным терминалом.

## Куда смотреть логи

Все журналы складываются в каталог **`logs/`** в корне проекта. Основные файлы:

| Файл | Компонент |
|------|-----------|
| `api.log` | Django API |
| `media_api.log` | Media API |
| `celery_worker.log`, `celery_beat.log`, `celery_tasks.log` | Celery |
| `nginx-access.log`, `nginx-error.log` | nginx (после `install-nginx`) |
| `redis.log` | Redis (после `install-redis`) |
| `client-dev.log` | Vite dev-сервер |
| `client-browser.log` | ошибки из браузера (**только с JWT**; гость и зависон до входа сюда не пишут) |
| `ollama-serve.log` | локальный LLM serve (фон, `start-ollama`, служба) |
| `jupyter.log` | JupyterLab |

Логи задач модулей Celery — в **`celery_tasks.log`** и **`celery_beat.log`** (фильтр по имени логгера), не в устаревших подпапках `logs/modules/`. Просмотр: `ergoms logs celery-tasks <модуль>`, `ergoms logs celery-beat <модуль>`.

Если клиент открыт через nginx (`ergoms start-nginx-dev` / `ERGO_PROXY=nginx`), а DevTools пустой при вечной загрузке, смотрите **`nginx-access.log`**: какие hashed-файлы и API реально запрошены. Vite на порту 8001 здесь ни при чём — прокси отдаёт `core/client/dist`. Подробнее — [troubleshooting.md](troubleshooting.md#пустой-экран-за-nginx).

Если фоновый процесс завершается с ошибкой без вывода в терминал, откройте соответствующий файл журнала.

Если API и клиент установлены как системные службы, их журналы доступны через `ergoms logs <имя-службы>` — подробнее в [deployment.md](deployment.md).

## Channel layer при разработке

При одном процессе API (`ergoms dev`) достаточно **`CHANNEL_LAYER_BACKEND=memory`** (значение по умолчанию). Если запущено несколько worker API и push realtime не доходит до клиента — переключите на `postgres` или установите Redis (`REDIS_ENABLED=true`). См. [configuration.md](configuration.md#redis-и-несколько-процессов).

## Частые команды

Полный справочник — в [cli.md](cli.md). Чаще всего понадобятся:

```cmd
ergoms migrate-all
ergoms api createsuperuser
ergoms warmup-caches
```

`migrate-all` создаёт и применяет миграции схемы БД. `createsuperuser` добавляет учётную запись администратора для входа в систему; пароль обязан соответствовать политике `API_PASSWORD_*` из `.env`, обойти проверку нельзя. `warmup-caches` принудительно обновляет кэши, если после смены модулей API начал работать некорректно.

## Адреса в браузере

После запуска откройте **клиент** в системном браузере — http://localhost:8001 (основная точка входа). Cursor сам не открывает эти адреса во встроенной вкладке Browser Tab: это делают `ergoms install-extensions` и расширение ERGO MS User Config. Если вкладка всё ещё появляется, перезагрузите окно Cursor. Подробнее, что делает каждый сервис — в [README.md](../README.md#2-запуск-для-разработки).

| Адрес | Назначение |
|-------|------------|
| http://localhost:8001 | Vue-клиент: вход, меню, страницы модулей |
| http://localhost:8000 | API; документация — `/swagger/` или `/redoc/` |
| http://localhost:8003 | Media API — файлы (обычно не открывают вручную) |
| http://localhost:8002/lab | JupyterLab, если `ERGO_JUPYTER` не `none` |

Порты можно изменить в `.env`, если 8000–8003 уже заняты.

## См. также

| Вопрос | Документ |
|--------|----------|
| Справочник команд ergoms | [cli.md](cli.md) |
| Lifecycle-pipeline | [lifecycle-pipeline.md](lifecycle-pipeline.md) |
| Настройка `.env` и баз данных | [configuration.md](configuration.md) |
| Если что-то не запускается | [troubleshooting.md](troubleshooting.md) |
| Системные службы (Linux / Windows) | [deployment.md](deployment.md) |
