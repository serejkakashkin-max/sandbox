# Sandbox

Sandbox - самостоятельное Flask/WSGI-приложение для публикации экспериментальных инструментов. Сейчас в него включены главная страница "Экспериментальные инструменты", веб-инструмент TA "Аудитор инцидентов" и GD "Монитор релизов".

## Структура

- `app.py` - локальный запуск WSGI-приложения через Werkzeug.
- `wsgi.py` - единый entrypoint `wsgi:application`.
- `sandbox_app/` - корневое Flask-приложение Sandbox и шаблон главной страницы.
- `TA/` - оригинальный Flask-проект аудитора инцидентов.
- `GD/` - Flask-проект аналитического монитора релизов.
- `GD/cache/` - runtime-каталог snapshot-файлов монитора релизов; в Git хранится только `.gitkeep`.
- `cache/ta_incident_auditor/uploads/` - runtime-каталог для загружаемых Excel-файлов, создается автоматически.
- `deploy/` - готовые файлы запуска для рабочего Linux-хоста.

## Маршруты

- `GET /` - главная Sandbox.
- `GET /health` - JSON-статус приложения.
- `GET, POST /ta/incident-auditor/` - загрузка и список инцидентов TA.
- `GET /ta/incident-auditor/incident/<inc_id>` - карточка инцидента.
- `GET /ta/incident-auditor/export` - экспорт результата аудита в Excel.
- `GET /gd/release-monitor/` - мониторинг и статистика релизов.
- `GET /gd/release-monitor/health` - состояние snapshot GD.
- `GET /gd/release-monitor/force-refresh` - ручное обновление snapshot.
- `GET /gd/release-monitor/download/*.xlsx` - Excel-выгрузки GD.

## Переменные окружения

- `SANDBOX_HOST` - хост локального запуска, по умолчанию `127.0.0.1`.
- `SANDBOX_PORT` - порт локального запуска, по умолчанию `3535`.
- `SANDBOX_PUBLIC_PREFIX` - внешний префикс публикации, например `/releases/sandbox`.
- `SANDBOX_PARENT_URL` - ссылка "Главная" в breadcrumb, локально по умолчанию `/`, для публикации можно указать `/releases/`.
- `GD_RELEASE_MONITOR_SOURCE_URL` - HTTP endpoint, из которого GD получает snapshot релизов.
- `GD_AUTO_REFRESH_ENABLED` - включает фоновое обновление GD каждые 5 минут (`1`, `true`, `yes` или `on`).

Также поддерживается заголовок `X-Forwarded-Prefix`. Все URL, которые генерирует Flask, учитывают `SCRIPT_NAME`, поэтому приложение может работать за прокси, снимающим внешний префикс.

## Конфигурации запуска

### Локальная разработка

Для локальной разработки используются безопасные значения по умолчанию:

- `SANDBOX_HOST=127.0.0.1`
- `SANDBOX_PORT=3535`
- `SANDBOX_PUBLIC_PREFIX=`
- `SANDBOX_PARENT_URL=/`
- `GD_RELEASE_MONITOR_SOURCE_URL=https://oplot.sberbank.ru/releases/dashboard/release-monitor/status`
- `GD_AUTO_REFRESH_ENABLED=0`

Файл `.env.example` является документацией и примером локальных значений. Его не нужно переименовывать, приложение не загружает его автоматически. Локально Sandbox запускается на `127.0.0.1:3535`.

### Рабочий хост

Для рабочего Linux-хоста подготовлены:

- `deploy/sandbox.env` - production-переменные окружения без секретов.
- `deploy/sandbox.service` - systemd unit-файл для запуска через Waitress.

На рабочем хосте используется внешний префикс `/releases/sandbox`, а breadcrumb "Главная" ведет на `/releases/`. Sandbox работает на отдельном порту `3535`. Основное приложение `generator_releases` на порту `3434` не затрагивается. AI-agent/RAG-приложение на порту `8000` не затрагивается.

SYNGX будет настроен отдельно. Его конфигурация сейчас в репозиторий Sandbox не добавляется.

## Установка через uv

```powershell
uv sync --locked
```

Если нужно только runtime-окружение:

```powershell
uv sync --locked --no-dev
```

## Локальный запуск

```powershell
$env:SANDBOX_HOST = "127.0.0.1"
$env:SANDBOX_PORT = "3535"
$env:SANDBOX_PUBLIC_PREFIX = ""
uv run python app.py
```

После запуска приложение будет доступно на `http://127.0.0.1:3535/`.

Для самостоятельного локального запуска только GD используется пакетный entrypoint:

```powershell
uv run python -m GD.web_app
```

## Запуск через Waitress

```powershell
uv run waitress-serve --listen=127.0.0.1:3535 wsgi:application
```

Для будущей публикации за SYNGX:

```powershell
$env:SANDBOX_PUBLIC_PREFIX = "/releases/sandbox"
$env:SANDBOX_PARENT_URL = "/releases/"
uv run waitress-serve --listen=127.0.0.1:3535 wsgi:application
```

## Runtime-файлы

TA сохраняет загруженные Excel-файлы в `cache/ta_incident_auditor/uploads/`. GD сохраняет primary snapshot, last-good snapshot и диагностический журнал в `GD/cache/`. Содержимое обоих runtime-каталогов не предназначено для Git. Имена файлов TA проходят через `secure_filename`, чтобы имя загрузки не могло задать произвольный путь на сервере.

## Добавление нового инструмента

1. Добавить инструмент как отдельное Flask/WSGI-приложение или blueprint.
2. Подключить его в `wsgi.py` через `DispatcherMiddleware` или аналогичный WSGI-механизм.
3. Добавить карточку в список `modules` в `sandbox_app/__init__.py`.
4. Использовать `url_for` или `request.script_root` для всех ссылок.
5. Выполнить локальные проверки маршрута, карточки и работы с `SANDBOX_PUBLIC_PREFIX`.

## Известные ограничения TA

Оригинальный TA хранит текущий набор обработанных инцидентов в памяти процесса. После перезапуска данные сбрасываются. При нескольких процессах workers состояние не будет общим, поэтому на первом этапе приложение следует запускать одним процессом.

Файл `TA/incident_auditor.py` относится к desktop-версии на Tkinter. Он оставлен как исходный файл, но не импортируется из production entrypoint и не участвует в веб-запуске.
