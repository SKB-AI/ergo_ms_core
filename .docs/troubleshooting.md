# Решение проблем при установке

Ниже — типичные сбои при первой настройке ERGO MS и что с ними делать. Если ошибка не из этого списка, начните с проверки `.env`, `databases.yaml` и каталога `logs/`.

## Poetry: Permission denied

Сообщение вроде `[Errno 13] Permission denied` при установке Python-зависимостей часто связано с повреждённым кэшем Poetry.

На Windows удалите кэш и переустановите зависимости:

```cmd
rmdir /S /Q "%LOCALAPPDATA%\pypoetry\Cache"
ergoms python-install
```

На Linux или macOS:

```bash
rm -rf ~/.cache/pypoetry
ergoms python-install
```

## Команда ergoms не найдена

Утилита **ergoms** лежит в **`core/deployment/bin/`**: `ergoms.cmd` (Windows) и `ergoms` (Linux/macOS). Запускайте её из корня проекта или подпапки (проверка по текущему каталогу). На Linux `ergoms install-cli` дополнительно ставит симлинк `/usr/local/bin/ergoms` на этот файл, чтобы работал `sudo ergoms`. В System32 Windows по-прежнему ничего не копируется.

**В Cursor / VS Code** откройте профиль терминала **Project-Shell** (каталог `bin` уже в PATH) или выполняйте задачи с `cwd` = корень workspace.

Если команда не находится во внешнем терминале:

```cmd
cd путь\к\ergo_ms
core\deployment\bin\ergoms help
```

На Linux:

```bash
cd /path/to/ergo_ms
./core/deployment/bin/ergoms help
```

Локальный CLI — `core/deployment/bin/ergoms` (и `ergoms.cmd` на Windows). Он входит в репозиторий. На Linux `uninstall-cli` снимает только симлинк `/usr/local/bin/ergoms`, если он указывает на этот проект; файлы в `bin` не удаляются. Убедитесь, что `core/deployment/bin` в PATH (профиль Project-Shell в Cursor/VS Code) либо что системная ссылка уже создана.

Либо повторите полную настройку из README: `setup-full` проверит локальный CLI.

## База данных

Если API не удаётся подключить к PostgreSQL:

1. Убедитесь, что файл **`databases.yaml`** создан из примера и параметры `host`, `port`, `user`, `password`, `name` соответствуют вашей СУБД.
2. Создайте пустую базу с указанным именем, если её ещё нет.
3. Примените миграции командой **`ergoms db-migrate`**.

## Медленный первый запуск API

При первом запуске система строит кэши списка приложений, модулей и конфигурации Celery — это нормально. Ускорить повторные старты можно явным прогревом:

```cmd
ergoms warmup-caches
```

Команда **`ergoms dev`** сама вызывает прогрев, если кэш пустой или устарел — отдельно вызывать её не обязательно.

## Пустые каталоги core/ или modules/ после clone

Если `core/api`, `core/client` или `modules/<имя>/` пусты — не подтянуты submodule:

```cmd
git submodule update --init --recursive
```

Затем повторите `setup-full` или `ergoms install-deps`.

## WebSocket / realtime не работает между процессами

Симптом: уведомления или presence не приходят при нескольких worker API.

**Причина:** channel layer `memory` работает только в одном процессе.

**Что сделать:** в `.env` задайте `CHANNEL_LAYER_BACKEND=postgres` или установите Redis (`ergoms install-redis`, `REDIS_ENABLED=true`, `CHANNEL_LAYER_BACKEND=redis`). Перезапустите API. См. [configuration.md](configuration.md#redis-и-несколько-процессов).

## Redis не отвечает

Симптом: `ergoms test-redis` не возвращает `PONG`.

1. Установите и запустите: `ergoms install-redis` или `ergoms start-redis`.
2. Проверьте `REDIS_HOST`, `REDIS_PORT` в `.env`.
3. Журнал: `logs/redis.log` или `ergoms logs ergo_ms_redis`.

## Пустой экран за nginx {#пустой-экран-за-nginx}

Симптом: `http://<хост>` через `ergoms start-nginx-dev` крутит маску загрузки или показывает пустую оболочку, консоль браузера пустая. Через `ergoms start-client` (Vite) та же копия кода открывается.

Nginx отдаёт **сборку** `core/client/dist`, а не сервер разработки Vite. После правок клиента выполните `ergoms client-build` и обычное обновление страницы (F5). При `ERGO_PROXY=nginx` эта команда пересобирает сайт-конфиг и делает reload. `index.html` и `/shared/*` отдаются с `Cache-Control: no-store`, а в import map у `/shared/*.js` есть `?v=<сборка>`, чтобы после деплоя браузер сам взял новый ModuleBridge, а не чистил «изображения и файлы». Отдельный `reload-nginx` нужен, если менялся шаблон конфига nginx или `env/nginx.env` без сборки клиента.

Консоль пустая, потому что production-сборка подменяет `console.*`, а `logs/client-browser.log` принимает ошибки только с JWT. Гость и зависон до входа туда ничего не пишут.

Смотрите `logs/nginx-access.log`:

1. Совпадает ли hashed `index-*.js` с `core/client/dist/index.html`.
2. Запрашивается ли чанк страницы входа (`LoginPage-*.js`). Если есть оболочка старта и нет этого чанка — первый переход роутера не закончился.
3. Есть ли GET `/__client-boot?e=…` — короткий след старта без сессии.

Типичная причина вечной маски: в таблице маршрутов нет `/login`, а guard редиректит на имя `Login`. Vue Router не завершает `router.isReady()`.

При обновлении страницы кабинета (F5) форма входа не должна мелькать: cookie `ergo_session` значит, что refresh ещё не выполнен, и guard обязан дождаться restore, а не сразу открывать `/login`. Если форма всё же вспыхивает на секунду — сборка старая или guard шлёт на вход до `whenSessionReady()`.

Правило для агента: [`.cursor/rules/nginx-spa-boot.mdc`](../.cursor/rules/nginx-spa-boot.mdc).

## SSE обрывается за nginx

Симптом: поток `/api/realtime/stream/` или модульный `…/stream/` (чат) закрывается через короткое время / ответ не идёт по частям.

1. Проверьте конфиг: `ergoms test-nginx`.
2. Перезагрузите: `ergoms reload-nginx`.
3. В сгенерированном nginx для SSE должны быть `proxy_buffering off` и длинный `proxy_read_timeout` на `/api/realtime/stream/` и `location ~ ^/api/.+/stream/` — см. [realtime.mdc](../.cursor/rules/realtime.mdc).

## Docker Compose

Симптом: `ergoms docker-up` завершается с ошибкой или контейнер `api` перезапускается.

1. Проверьте, что Docker запущен: `docker compose version`.
2. Статус контейнеров: `ergoms docker-ps`; журнал: `ergoms docker-logs api` или `ergoms docker-logs postgres`.
3. База данных: `databases.yaml` и сгенерированный `core/deployment/docker/.compose.databases.yaml`; при `DOCKER_DATABASE=container` хост в yaml должен быть `localhost` (подменится на `postgres`).
4. Порт занят — смените `API_PORT` / `CLIENT_PORT` в `.env` или остановите portable-службы и хостовый `ergoms dev`.
5. После правки `celery_workers.yaml`: `ergoms docker-gen-workers` и `ergoms docker-restart`.
6. Миграции: `ergoms docker-migrate`.

Долгий повторный `docker-init`:

1. По умолчанию `DOCKER_BUILD_POLICY=if-missing` — build пропускается, если образы уже есть.
2. Для принудительной пересборки: `DOCKER_BUILD_POLICY=always` или `ergoms docker-build`.
3. Скачать зависимости без кэша: `DOCKER_DEPS_CACHE=off`, затем `ergoms docker-build -- --no-cache`.
4. Очистить `virtual_env/cache/docker-cache/` или тома `*_poetry_venv`, `*_node_modules`.

Подробнее — [docker.md](docker.md).

## Ошибка парсинга PowerShell (.ps1) {#ошибка-парсинга-powershell-ps1}

Симптом при `ergoms` на Windows: в терминале `ParserError`, «The string is missing the terminator», вместо русского текста — `ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ð°Ñ` и т.п. Часто проявляется при `warmup-caches-if-needed`, `install-nginx`, `start-redis`, службах — когда вызывается `internal_dispatch.ps1`.

**Причина:** файл `.ps1` в `core/deployment/` сохранён в UTF-8 **без BOM**. Windows PowerShell 5.1 читает его в системной кодировке и ломает строки с кириллицей.

**Что сделать:**

```cmd
ergoms ps1-encoding-check --fix
```

Повторите команду, которая упала. Для профилактики перед коммитом: `ergoms core-rules-check` (включает проверку BOM).

Подробнее — [lifecycle-pipeline.md](lifecycle-pipeline.md#powershell-и-кириллица-windows).

## Cursor сам открывает страницы во встроенном браузере

Ссылка `http://localhost:…` из терминала или чата открывается во вкладке Browser Tab, а не в системном браузере.

В Cursor это не настройка `workbench.browser.openLocalhostLinks`. Переключатель «Open Local Links in Cursor Browser» лежит во внутреннем хранилище. Его выключают `ergoms install-extensions` и расширение ERGO MS User Config при открытии проекта. Затем перезагрузите окно (**Developer: Reload Window**).

Не задавайте `workbench.externalBrowser` значение `"default"`: Cursor тогда не открывает внешние `https://` ссылки. Системный браузер — это отсутствие ключа, а не строка `"default"`.

## См. также

| Вопрос | Документ |
|--------|----------|
| Настройка `.env` и баз данных | [configuration.md](configuration.md) |
| Справочник команд ergoms | [cli.md](cli.md) |
| Docker Compose | [docker.md](docker.md) |
| Запуск для разработки | [development.md](development.md) |
| Пустой экран за nginx (агент) | [`.cursor/rules/nginx-spa-boot.mdc`](../.cursor/rules/nginx-spa-boot.mdc) |
| Lifecycle-pipeline | [lifecycle-pipeline.md](lifecycle-pipeline.md) |
