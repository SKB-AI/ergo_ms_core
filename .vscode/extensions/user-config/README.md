# ERGO MS User Config Extension

Расширение для VS Code/Cursor, которое автоматически применяет проектные настройки из файлов:
- `.vscode/user_settings.json` -> настройки workspace
- `.vscode/user_keybindings.json` -> глобальные keybindings
- политика встроенного браузера Cursor (при установке и при открытии проекта)

## Возможности

- **Автоматическое применение настроек** при открытии проекта
- **Автоматическое отслеживание изменений** в файлах конфигурации
- **Поддержка VS Code и Cursor**
- **Умное слияние** keybindings (без дубликатов)

## Установка

### Через ergoms (рекомендуется)

```bash
ergoms install-extensions
```

### Вручную

**Linux/macOS:**
```bash
bash .vscode/extensions/user-config/install.sh
```

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File .vscode/extensions/user-config/install.ps1
```

## Удаление

### Через ergoms

```bash
ergoms uninstall-extensions
```

### Вручную

**Linux/macOS:**
```bash
bash .vscode/extensions/user-config/uninstall.sh
```

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File .vscode/extensions/user-config/uninstall.ps1
```

## Файлы конфигурации

### user_settings.json

Настройки, которые будут применены к workspace. Формат аналогичен `settings.json`:

```json
{
    "git.detectSubmodules": true,
    "git.autoRepositoryDetection": true,
    "editor.fontSize": 14
}
```

### user_keybindings.json

Горячие клавиши, которые будут добавлены в глобальные keybindings пользователя:

```json
[
    {
        "key": "ctrl+shift+b",
        "command": "workbench.action.tasks.build"
    },
    {
        "key": "ctrl+i",
        "command": "composerMode.agent"
    }
]
```

## Команды

Расширение добавляет следующие команды (доступны через Command Palette):

- **ERGO: Apply User Settings** - применить только настройки
- **ERGO: Apply User Keybindings** - применить только keybindings
- **ERGO: Apply All User Config** - применить все настройки
- **ERGO: Remove User Keybindings** - удалить все ERGO MS keybindings

## Как это работает

1. При открытии проекта расширение активируется, если находит файлы `user_settings.json` или `user_keybindings.json`
2. Настройки из `user_settings.json` применяются к workspace через VS Code API
3. Keybindings из `user_keybindings.json` добавляются в глобальный `keybindings.json` пользователя (без дубликатов)
4. В Cursor дополнительно выключается автооткрытие вкладки Browser Tab. Это не обычный `settings.json`: Cursor хранит переключатель в своём внутреннем хранилище.
5. При изменении файлов конфигурации расширение автоматически применяет изменения

## Маркировка keybindings

При добавлении keybindings в глобальный файл, расширение маркирует их комментарием `// ERGO MS`:

```json
[
    { "key": "ctrl+s", "command": "workbench.action.files.save" },
    // ERGO MS
    { "key": "ctrl+i", "command": "composerMode.agent" },
    // ERGO MS
    { "key": "ctrl+shift+b", "command": "workbench.action.tasks.build" }
]
```

Это позволяет:
- Легко идентифицировать keybindings, добавленные расширением
- Удалить все ERGO MS keybindings командой **ERGO: Remove User Keybindings**

## Remote режим (WSL, SSH, Container)

Расширение поддерживает Remote режим. Для полной функциональности рекомендуется установить расширение **на локальную машину**.

### Автоматическая установка на локальную машину

1. Откройте Command Palette (`Ctrl+Shift+P`)
2. Выполните команду **ERGO: Install Extension Locally (for Remote mode)**
3. Нажмите **Перезагрузить** для активации

После этого расширение будет работать автоматически:
- **Settings** применяются к workspace через VS Code API
- **Keybindings** применяются автоматически к локальному `keybindings.json`
- Файлы конфигурации читаются из удалённого workspace через VS Code API

### Если расширение работает только на Remote сервере

Если расширение установлено только на Remote сервере:
- **Settings** применяются автоматически
- **Keybindings** НЕ могут быть применены автоматически (они хранятся на локальной машине)

В этом случае при попытке применить keybindings появится предложение установить расширение локально.

## Примечания

- Keybindings добавляются в глобальные настройки пользователя, чтобы работать во всех режимах
- Keybindings маркируются комментарием `// ERGO MS` для идентификации
- Команда удаления удаляет только маркированные keybindings
- Настройки применяются только к текущему workspace
- Remote режим (WSL, SSH, Container) полностью поддерживается

