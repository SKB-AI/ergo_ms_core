# Структура проекта

Корень репозитория — это не только исходники, но и точка сборки всей системы. Здесь лежат конфигурация, виртуальное окружение, логи и ссылки на подключённые модули.

## Основные каталоги

**`core/api/`**, **`core/client/`**, **`core/media_api/`** — части ядра как **git-submodule** (отдельные репозитории в `.gitmodules`). **`core/.github/`** — submodule community-репозитория организации (CONTRIBUTING, SECURITY, шаблоны issues/PR); инициализируется вместе с ядром при `setup-full` и `ergoms update-submodules`. **`core/deployment/`** — в корневом репозитории: скрипты установки и запуска, файл `commands.conf` с командами ergoms.

**`modules/<имя>/`** — доменные модули. Каждый модуль — submodule со своим git-репозиторием; внутри типично пары `api/` и `client/`. Каталог точек расширения — [modules.md](modules.md).

**`virtual_env/`** — Python-окружение (`python/`), portable-пакеты (**`packages/redis/`**, **`packages/nginx/`**), кэш Celery, ресурсы GeoIP. Второй venv в проекте создавать не нужно.

**`logs/`** — журналы API, Celery, media_api, nginx, Redis и др. При отладке сюда стоит заглянуть в первую очередь. Задачи модулей Celery — в `celery_tasks.log` и `celery_beat.log`, не в подпапках `logs/modules/`.

## Конфигурационные файлы

В корне проекта:

- **`.env`** — общие настройки и режимы `ERGO_*` (из `.env.example`, в том числе при `setup-full`).
- **`env/`** — фрагменты `nginx`, `docker`, `jupyter`, `smtp`, `logging`, `mcp`, `media`, `realtime`, `cache`, `celery` (из `env/*.example`).
- **`databases.yaml`** — каталог подключений SQL и Redis (из `databases.yaml.example`).
- **`celery_workers.yaml`** — какие очереди обслуживает каждый исполнитель Celery.

У модулей могут быть свои **`.env`** и **`ergoms.conf`** — локальные переопределения и команды, которые ergoms регистрирует с префиксом имени модуля.

Пункты бокового меню модуль регистрирует в **миграции API** (`MenuMigrationHelper` → таблицы `MenuItem` / `MenuSeparator`). Порядок и видимость настраиваются в админ-панели CMS (`MenuPanel.vue`). Маршруты страниц — `client/js/routes.js`. Подробнее — [architecture.md](architecture.md#боковое-меню).

## Документация и правила

Каталог **`.docs/`** — описание архитектуры, настройки и команд для людей. Оглавление — в [README.md](../README.md#документация) в корне репозитория.

**`.cursor/rules/`** — инструкции для AI-ассистента в Cursor; по смыслу пересекаются с `.docs/`, но короче и с примерами «плохо / хорошо». Стиль правил и документов — [`writing-docs-and-rules.mdc`](../.cursor/rules/writing-docs-and-rules.mdc).

Технические заметки в дереве **`core/`**:

| Файл | О чём |
|------|--------|
| [`core/api/README.md`](../core/api/README.md) | Django API, структура, запуск |
| [`core/client/README.md`](../core/client/README.md) | Vue-клиент, компоненты |
| [`core/media_api/README.md`](../core/media_api/README.md) | файловый сервис |
| [`core/deployment/logic.md`](../core/deployment/logic.md) | ergoms, commands.conf, GeoIP, Redis, nginx, TLS, realtime за reverse proxy |
| [`.cursor/rules/deployment-infra.mdc`](../.cursor/rules/deployment-infra.mdc) | Redis, nginx, TLS — правила для агента Cursor |
| `modules/<имя>/logic.md` | контракты конкретного модуля (если есть) |

## Куда смотреть дальше

| Вопрос | Документ |
|--------|----------|
| Как устроена система в целом | [architecture.md](architecture.md) |
| Чем модуль может расширять ядро | [modules.md](modules.md) |
| Как настроить `.env` и базы данных | [configuration.md](configuration.md) |
| Как запускать проект при разработке | [development.md](development.md) |
| Справочник команд ergoms | [cli.md](cli.md) |
| Если установка завершилась с ошибкой | [troubleshooting.md](troubleshooting.md) |
| Системные службы на Linux / Windows | [deployment.md](deployment.md) |
