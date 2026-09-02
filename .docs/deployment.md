# Развёртывание системных служб (Linux / Windows)

На Linux ERGO MS можно зарегистрировать как набор служб **systemd**, чтобы API, клиент, Celery и media_api запускались при старте сервера и перезапускались после сбоев. На Windows — через NSSM. **Основной способ управления — `ergoms`**, а не прямой вызов скриптов развёртывания.

Альтернатива изолированному стеку в контейнерах — **Docker Compose** (`ergoms docker-*`). См. [docker.md](docker.md).

Путь к корню проекта задаётся переменной **`ERGO_ROOT`** в файле `core/deployment/wrappers/ergo_ms.env` или передаётся флагом **`--root`** при установке.

## Установка и управление службами

Из корня репозитория выполните нужную команду через **ergoms**:

```bash
ergoms install-services
ergoms start
ergoms stop
ergoms restart
ergoms status
ergoms uninstall-services --purge
```

Команды **`install-services`** и **`uninstall-services`** управляют службами ОС одной логикой: ядро ставит/снимает свои unit’ы, а подключённые модули с `host_lifecycle.yaml` — свои (`install_service_commands` / `uninstall_service_commands`) без правок ядра под каждый модуль. При `ERGO_DB=portable_postgres` `install-services` также регистрирует службу portable PostgreSQL (как Redis и Meilisearch при своих режимах). То же делает `setup-full` после установки кластера. Флаг **`--purge`** у uninstall дополнительно удаляет связанные данные конфигурации — используйте только если уверены, что конфигурацию можно удалить. Portable-пакет модуля (бинарник) uninstall-services не трогает. Полный каталог модульных точек расширения — [modules.md](modules.md).

Для первичной настройки CLI в PATH:

```bash
cd /path/to/ergo_ms
ergoms install-cli
ergoms start
ergoms status
```

CLI лежит в `core/deployment/bin`. На Linux `install-cli` ставит симлинк `/usr/local/bin/ergoms` на этот файл (нужны права root, если каталог недоступен на запись). `uninstall-cli` снимает только эту ссылку, если она указывает на текущий проект; сами файлы в `bin` не удаляются. См. [cli.md](cli.md).

### Низкоуровневые скрипты (редко)

При отладке развёртывания допустим прямой вызов `core/deployment/linux/ergo_ms.sh` или `core/deployment/windows/ergo_ms.ps1` с правами администратора. В повседневной работе и в `.vscode/tasks.json` используйте только **`ergoms …`**.
## Имена служб

| Служба systemd | Компонент |
|----------------|-----------|
| `ergo_ms_api_dev` | Django API |
| `ergo_ms_client_dev` | Vue-клиент (режим разработки) |
| `ergo_ms_celery_worker_all` | Celery worker |
| `ergo_ms_celery_beat` | Celery beat |
| `ergo_ms_media_api` | Media API |
| `ergo_ms_redis` | Redis (Linux, после `install-redis-service`) |
| `ergo_ms_nginx` | nginx (Linux и Windows, после `install-nginx-service`) |

### Windows (те же имена `ergo_ms_*`)

| Служба Windows | Компонент |
|----------------|-----------|
| `ergo_ms_api_dev` | Django API |
| `ergo_ms_client_dev` | Vue-клиент |
| `ergo_ms_celery_worker_all` | Celery worker |
| `ergo_ms_celery_beat` | Celery beat |
| `ergo_ms_media_api` | Media API |
| `ergo_ms_redis` | Redis (после `install-redis-service`) |
| `ergo_ms_nginx` | nginx (после `install-nginx-service`) |

Точные имена могут отличаться, если вы меняли конфигурацию исполнителей в `celery_workers.yaml` или portable-пакетов; список актуальных служб покажет `ergoms status`.

## Просмотр логов

Через systemd:

```bash
journalctl -u ergo_ms_api_dev -n 500 -f
```

Через ergoms, если утилита установлена:

```bash
ergoms logs ergo_ms_api_dev
```

Дополнительно файловые журналы пишутся в каталог **`logs/`** в корне проекта — см. [development.md](development.md).

## Windows

Аналогичные сценарии для Windows — **`core/deployment/windows/ergo_ms.ps1`**: установка служб (`install`), управление через `ergoms start` / `stop` / `status`. Имена служб Windows: `ergo_ms_api_dev`, `ergo_ms_client_dev`, `ergo_ms_celery_worker_all`, `ergo_ms_celery_beat`, `ergo_ms_media_api`, при необходимости `ergo_ms_redis`, `ergo_ms_nginx`.

Сначала выполните полную первичную настройку (`setup-full`) или установку служб (`install`), затем управляйте ими через ergoms. Опциональные nginx и Redis — [cli.md](cli.md#nginx-опционально), [configuration.md](configuration.md#redis-и-несколько-процессов).

## См. также

| Вопрос | Документ |
|--------|----------|
| Запуск для разработки (без служб) | [development.md](development.md) |
| Docker Compose | [docker.md](docker.md) |
| Справочник команд ergoms | [cli.md](cli.md) |
| Если служба не запускается | [troubleshooting.md](troubleshooting.md) |
