# AWG Web GUI

Веб-интерфейс для управления серверами **AmneziaWG 1.5 и 2.0** через Docker-контейнеры `amnezia-awg` / `amnezia-awg2`.

Позволяет из браузера добавлять серверы, управлять клиентами, генерировать конфигурации и QR-коды, а также синхронизировать пиры с `wg0.conf` / `awg0.conf` на сервере.

## Возможности

- Поддержка **AmneziaWG 1.5** (контейнер `amnezia-awg`, порт `8723`, интерфейс `wg0`) и **AmneziaWG 2.0** (контейнер `amnezia-awg2`, порт `9723`, интерфейс `awg0`).
- Добавление серверов по SSH с ключом (пароли не поддерживаются, только key-based auth).
- Автоматическое определение `subnet` из `Address` в серверном конфиге.
- Чтение публичного ключа сервера и AmneziaWG-параметров (`Jc`, `Jmin`, `Jmax`, `S1`, `S2`, `H1`–`H4` и др.) из `wg0.conf` / `awg0.conf`.
- Авто-назначение IP-адресов клиентов внутри подсети сервера.
- Генерация клиентских `.conf`, QR-кодов и URI для подключения.
- Синхронизация существующих пиров из серверного конфига в GUI.
- Backup серверного конфига перед каждым изменением.

## Требования

- Python 3.10+
- SSH-ключ с доступом `root` (или другой пользователь с правами на `docker`) к серверу с AmneziaWG.
- На целевом сервере должен быть запущен Docker-контейнер `amnezia-awg` или `amnezia-awg2`.

## Установка и запуск

### 1. Клонирование и создание окружения

```bash
git clone https://github.com/dogoninpavel/awg-web-gui.git
cd awg-web-gui

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Запуск dev-сервера

```bash
python app.py
```

По умолчанию сервер стартует на `http://127.0.0.1:5173`.

### 3. Открытие в браузере

Перейдите по адресу `http://127.0.0.1:5173`.

Логин по умолчанию:
- **Логин:** `admin`
- **Пароль:** `admin`

> ⚠️ Сразу после первого входа смените пароль и `AWG_SECRET_KEY`.

## Конфигурация

Поведение приложения можно настроить через переменные окружения:

| Переменная | Описание | Значение по умолчанию |
| :--- | :--- | :--- |
| `AWG_SECRET_KEY` | Секретный ключ Flask для сессий | `dev-change-me` |
| `AWG_DATA_DIR` | Папка с JSON-файлами данных | `~/awg-web-gui-data` |

Пример запуска с `.env`:

```bash
# Windows PowerShell:
$env:AWG_SECRET_KEY="your-random-secret-here"
$env:AWG_DATA_DIR="C:\Users\pavel\awg-web-gui-data"
python app.py

# Linux/macOS:
export AWG_SECRET_KEY="your-random-secret-here"
export AWG_DATA_DIR="/root/awg-web-gui-data"
python app.py
```

## Как управлять

### Добавление сервера

1. Нажмите **Add Server**.
2. Заполните:
   - **Name** — произвольное имя, например `admrus-2.0`.
   - **Host** — IP или домен сервера.
   - **Version** — `1.5` или `2.0`.
   - **SSH Key** — путь до приватного SSH-ключа, например `C:\Users\pavel\.ssh\awg_web_gui_admrus_ed25519`.
   - **WG Port** — `8723` для 1.5, `9727` для 2.0 (подставляется автоматически по версии).
   - **Endpoint** — внешний IP/домен, который увидят клиенты.
3. Если поле **Subnet** оставить пустым, приложение подтянет его автоматически из `Address` в серверном `wg0.conf` / `awg0.conf`.
4. Нажмите **Save**. GUI проверит SSH, Docker-контейнер и доступность конфига.

### Добавление клиента

1. Выберите сервер.
2. Нажмите **Add Client**.
3. Введите имя клиента.
4. GUI автоматически:
   - сгенерирует ключевую пару и pre-shared key;
   - назначит свободный IP из подсети сервера;
   - запишет пир в `wg0.conf` / `awg0.conf` на сервере;
   - перезапустит контейнер и дождётся появления пира в `wg show`.
5. После создания доступны:
   - скачивание `.conf`;
   - QR-код;
   - URI для импорта в AmneziaWG.

### Синхронизация с сервером

Если на сервере уже есть клиенты, созданные вне GUI, нажмите **Sync** рядом с сервером. GUI прочитает пиров из конфига и импортирует их (без приватных ключей — только публичные ключи, allowed IPs и комментарии).

### Удаление клиента

Нажмите **Delete** у клиента. GUI удалит пир из серверного конфига, перезапустит контейнер и проверит, что пир исчез из `wg show`.

### Backup конфига

Перед каждой записью в `wg0.conf` / `awg0.conf` GUI автоматически создаёт backup с timestamp: `/opt/amnezia/awg/wg0.conf.bak-YYYYMMDD-HHMMSS` (внутри контейнера).

## Структура проекта

```
awg-web-gui/
├── app.py              # Flask backend + логика работы с SSH/Docker
├── requirements.txt    # Зависимости Python
├── static/             # Frontend JS, CSS, QR-генерация
│   ├── app.js
│   └── style.css
├── templates/
│   └── index.html      # Главная страница
└── .venv/              # Виртуальное окружение (не в git)
```

## Безопасность

- Используйте только SSH-key аутентификацию; пароли не передаются и не хранятся.
- Смените дефолтный пароль `admin/admin` перед выходом в продакшен.
- Установите свой `AWG_SECRET_KEY`, иначе сессии Flask можно подделать.
- Для удалённого доступа поставьте реверс-прокси (nginx, NPM, Traefik) с HTTPS; не открывайте порт `5173` наружу.

## Лицензия

MIT



### SSH key или пароль

В поле `Путь к SSH ключу` указывается путь внутри контейнера. При стандартном volume `./ssh:/ssh:ro` это обычно:

```text
/ssh/id_ed25519
```

Для удобства dev/test, если в UI ввести привычный host path:

```text
/root/.ssh/id_ed25519
```

а внутри контейнера есть `/ssh/id_ed25519`, приложение автоматически использует `/ssh/id_ed25519`.

Можно не указывать ключ и использовать поле `SSH пароль вместо ключа`. Для этого в Docker image установлен `sshpass`; пароль хранится в SQLite вместе с сервером, так что это удобно для разработки, но для production лучше ключи.

### Private key существующих клиентов

WireGuard/AmneziaWG server config хранит только `PublicKey`, `PresharedKey` и `AllowedIPs` peer'а. Client `PrivateKey` криптографически не восстанавливается из `PublicKey`.

При `Sync` GUI пытается импортировать private keys из Amnezia `clientsTable`, если они там есть. Если `clientsTable` пустой или не содержит private key, у существующего клиента останется `нет private key`; его можно вручную вставить в форме редактирования клиента. После этого станут доступны экспорт config и QR.


## SQLite, мониторинг и traffic accounting

Начиная с Docker/MVP-версии приложение хранит рабочие данные в SQLite:

```text
/data/awg-web-gui.db
```

При первом запуске старые JSON-файлы `servers.json`, `clients.json`, `users.json` автоматически мигрируются в SQLite. JSON-файлы продолжают обновляться как удобный legacy-export для ручной проверки и резервного копирования, но основной источник данных — SQLite.

### Что хранится в базе

| Таблица | Назначение |
| :--- | :--- |
| `servers` | добавленные AWG-серверы и параметры SSH/Docker |
| `clients` | клиенты/пиры и данные для генерации конфигов |
| `users` | локальные пользователи GUI |
| `client_stats` | last handshake, endpoint, online/offline, RX/TX, total RX/TX |
| `events` | события миграций, polling, ошибок опроса |
| `settings` | служебные настройки |

### Мониторинг клиентов

Фоновый poller опрашивает каждый сервер через SSH:

```bash
# AWG 1.5
docker exec amnezia-awg wg show wg0 dump

# AWG 2.0
docker exec amnezia-awg2 awg show awg0 dump
```

Из `dump` читаются:

- `endpoint` клиента;
- `latest_handshake`;
- текущие `transfer_rx` / `transfer_tx`;
- online/offline статус.

Клиент считается online, если последний handshake был не старше `AWG_ONLINE_THRESHOLD` секунд.

### Настройки polling

| Переменная | Значение по умолчанию | Описание |
| :--- | :---: | :--- |
| `AWG_POLL_INTERVAL` | `30` | период фонового опроса серверов, секунд |
| `AWG_ONLINE_THRESHOLD` | `180` | сколько секунд после handshake клиент считается online |
| `AWG_ENABLE_POLLER` | `1` | `0` отключает фоновый poller |

### Подсчёт трафика

WireGuard/AWG counters могут сбрасываться после restart контейнера. Поэтому GUI хранит:

- `transfer_rx` / `transfer_tx` — текущие значения из runtime;
- `total_rx` / `total_tx` — накопленные значения в SQLite.

Если новый counter меньше предыдущего, приложение считает это reset и добавляет новое значение как delta.


## Готовый Docker image

После push в `main` GitHub Actions собирает образ:

```text
ghcr.io/sllikmll/awg-web-gui:latest
```

Пример запуска без локальной сборки:

```yaml
services:
  awg-web-gui:
    image: ghcr.io/sllikmll/awg-web-gui:latest
    container_name: awg-web-gui
    restart: unless-stopped
    environment:
      AWG_SECRET_KEY: "change-me-long-random-string"
      AWG_DATA_DIR: /data
      AWG_POLL_INTERVAL: "30"
      AWG_ONLINE_THRESHOLD: "180"
    ports:
      - "8095:5173"
    volumes:
      - ./data:/data
      - ./ssh:/ssh:ro
```

## Запуск в Docker

В репозитории есть `Dockerfile` и пример `docker-compose.example.yml`.

### Быстрый старт

```bash
git clone https://github.com/sllikmll/awg-web-gui.git
cd awg-web-gui
cp docker-compose.example.yml docker-compose.yml
mkdir -p data ssh
```

Положите SSH-ключ для доступа к AmneziaWG-серверам в `./ssh/`, например:

```bash
cp ~/.ssh/id_ed25519 ./ssh/id_ed25519
chmod 600 ./ssh/id_ed25519
```

В `docker-compose.yml` замените `AWG_SECRET_KEY` на длинную случайную строку и запустите:

```bash
docker compose up -d --build
```

По умолчанию Web UI будет доступен на:

```text
http://127.0.0.1:8095
```

Если ключ смонтирован как `./ssh:/ssh:ro`, то в форме добавления сервера указывайте путь к ключу внутри контейнера:

```text
/ssh/id_ed25519
```

Данные приложения хранятся в bind mount `./data:/data` и переживают пересоздание контейнера.

### Проверка

```bash
docker compose ps
docker logs --tail 50 awg-web-gui
curl -I http://127.0.0.1:8095/
```

## UI Screenshots

![AWG Web GUI Clients View](https://raw.githubusercontent.com/sllikmll/awg-web-gui/main/docs/ui-clients.png)


## Features

- **Auto-detect server settings** - No need to manually enter version, port, subnet, or DNS
- **SSH key or password auth** - Flexible authentication options
- **Auto-sync existing peers** - Import existing clients and private keys automatically
- **Client management** - Add, edit, delete clients with QR code/config export
- **Monitoring & traffic accounting** - Real-time status, handshake, RX/TX stats
- **SQLite backend** - Persistent storage for servers, clients, and stats
- **Docker deployment** - Ready-to-use Docker image with compose example
- **Periodic metadata refresh** - Auto-update server settings from remote config
- **Health diagnostics** - Check SSH, NAT, IP forwarding status
- **Web UI** - Clean, responsive interface for all operations
