# AWG Web GUI

Веб-интерфейс для управления серверами **AmneziaWG 1.5 и 2.0** через Docker-контейнеры `amnezia-awg` / `amnezia-awg2`.

Позволяет из браузера добавлять серверы, синхронизировать существующие peers, управлять клиентами, генерировать конфигурации и QR-коды, смотреть мониторинг/трафик, запускать диагностику и работать с backup конфигов.

## Скриншот

![AWG Web GUI Clients View](https://raw.githubusercontent.com/sllikmll/awg-web-gui/main/docs/ui-clients.png)

## Возможности

- **Автоопределение настроек сервера** — версия AWG, контейнер, interface, config path, UDP port, subnet и DNS читаются из remote config.
- **Аутентификация по SSH-ключу или паролю** — можно использовать `/ssh/id_ed25519` из Docker volume или SSH password для dev/test.
- **Автосинхронизация существующих клиентов** — после добавления сервера GUI сразу импортирует peers из `wg0.conf` / `awg0.conf`.
- **Импорт private keys из `clientsTable`** — если Amnezia сохранила private key клиента, GUI подтянет его и включит config/QR export.
- **Ручное восстановление private key** — можно вставить private key в Edit client или импортировать существующий client `.conf`.
- **Управление клиентами** — добавление, редактирование, удаление, временное отключение/включение, QR-код и `.conf` export.
- **Мониторинг и учёт трафика** — online/offline, latest handshake, endpoint, RX/TX и накопительный traffic accounting.
- **SQLite backend** — основной источник данных `/data/awg-web-gui.db`; legacy JSON export остаётся только для debug/backup.
- **Docker deployment** — готовый Dockerfile, compose example и GHCR image.
- **Фоновое обновление metadata** — периодически обновляет subnet/port/DNS из remote config, не изменяя сам VPN config.
- **Health diagnostics** — проверка SSH, Docker container, runtime AWG, `ip_forward`, NAT и subnet mismatch.
- **/32 subnet inference** — если интерфейс хранит `Address = x.x.x.1/32`, GUI выводит эффективную `/24` сеть из peer `AllowedIPs` и не ломает Diagnose ложным subnet mismatch.
- **Custom config path preservation** — metadata refresh сохраняет вручную исправленные `container` / `interface` / `config_path` и не откатывает новые AWG layout-пути обратно на старые `/opt/amnezia/awg/...`.
- **Remote config backups** — backup перед изменениями, просмотр backup-файлов и restore из UI.
- **Fleet import** — импорт списка серверов JSON array с auto-detect AWG 1.5/2.0 и auto-sync peers.
- **Журнал событий** — sync, polling, ошибки, import, restore и другие операции пишутся в `events`.
- **Security cleanup** — password hashing через Werkzeug, смена пароля в UI, секреты маскируются в публичных API ответах.
- **Веб-интерфейс** — простой адаптивный UI для всех операций.

## Требования

- Python 3.10+
- Docker на сервере, где запускается GUI.
- SSH-доступ к AWG-серверам.
- На целевом AWG-сервере должен быть запущен Docker-контейнер:
  - AWG 1.5: `amnezia-awg`, interface `wg0`, config `/opt/amnezia/awg/wg0.conf`;
  - AWG 2.0: `amnezia-awg2`, interface `awg0`, config `/opt/amnezia/awg/awg0.conf`.

## Быстрый запуск через Docker

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
4. Нажмите **Save**. GUI проверит SSH, Docker-контейнер и доступность конфига. Для новых layout-серверов поддерживаются не только старые пути `/opt/amnezia/awg/...`, но и `/etc/amneziawg/wg0.conf` и `/etc/amneziawg2/awg0.conf`.

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

- Для production предпочитайте SSH-ключи. Парольная аутентификация поддерживается для dev/test, но пароль хранится в SQLite вместе с записью сервера.
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
>>>>>>> 0398a4d (fix: preserve custom AWG layouts in refresh and diagnose)

```bash
git clone https://github.com/sllikmll/awg-web-gui.git
cd awg-web-gui
cp docker-compose.example.yml docker-compose.yml
mkdir -p data ssh
```

Положите SSH-ключ для доступа к AmneziaWG-серверам в `./ssh/`:

```bash
cp ~/.ssh/id_ed25519 ./ssh/id_ed25519
chmod 600 ./ssh/id_ed25519
```

В `docker-compose.yml` замените `AWG_SECRET_KEY` на длинную случайную строку и запустите:

```bash
docker compose up -d --build
```

Web UI по умолчанию:

```text
http://127.0.0.1:8095
```

Логин по умолчанию:

```text
admin / admin
```

> ⚠️ После первого входа обязательно смените пароль и `AWG_SECRET_KEY`, если GUI доступен не только локально.

## Готовый GHCR image

После push в `main` GitHub Actions собирает образ:

```text
ghcr.io/sllikmll/awg-web-gui:latest
```

Пример compose:

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
      AWG_METADATA_REFRESH_INTERVAL: "300"
    ports:
      - "8095:5173"
    volumes:
      - ./data:/data
      - ./ssh:/ssh:ro
```

## Конфигурация

| Переменная | По умолчанию | Описание |
| :--- | :---: | :--- |
| `AWG_SECRET_KEY` | `dev-change-me` | Flask secret key для session cookies |
| `AWG_DATA_DIR` | `~/awg-web-gui-data` | Директория с SQLite DB и legacy JSON export |
| `AWG_POLL_INTERVAL` | `30` | Интервал фонового polling, секунд |
| `AWG_ONLINE_THRESHOLD` | `180` | Сколько секунд после handshake клиент считается online |
| `AWG_METADATA_REFRESH_INTERVAL` | `300` | Как часто обновлять subnet/port/DNS из remote config |
| `AWG_ENABLE_POLLER` | `1` | `0` отключает background poller |
| `PORT` | `5173` | HTTP port внутри контейнера |

## Как пользоваться

### Добавление сервера

1. Нажмите **Добавить сервер**.
2. Укажите `name`, `host`, SSH user/port и SSH key или SSH password.
3. Оставьте auto-detect включённым.
4. GUI сам проверит AWG 2.0 и AWG 1.5 контейнеры.
5. Если найдены оба варианта, будут добавлены оба сервера.
6. После добавления автоматически выполнится sync existing peers.

При standard Docker volume `./ssh:/ssh:ro` путь к ключу внутри GUI обычно:

```text
/ssh/id_ed25519
```

Если случайно ввести host path вроде `/root/.ssh/id_ed25519`, приложение попробует автоматически сопоставить его с `/ssh/id_ed25519`.

### Синхронизация существующих peers

Кнопка **Sync** читает `[Peer]` blocks из remote config и импортирует их в SQLite.

GUI также пытается подтянуть private keys из Amnezia `clientsTable`, если они там есть.

### Private key существующих клиентов

WireGuard/AmneziaWG server config хранит только:

```ini
PublicKey = ...
PresharedKey = ...
AllowedIPs = ...
```

Client `PrivateKey` криптографически невозможно восстановить из `PublicKey`.

Поэтому для existing clients есть три варианта:

1. **clientsTable содержит private key** — GUI подтянет его автоматически при sync/enrich.
2. **private key известен пользователю** — вставьте его в **Edit client**.
3. **есть готовый client `.conf`** — используйте **Импорт .conf**.

Без private key GUI может показать peer и мониторинг, но не сможет сгенерировать рабочий config/QR.

### Добавление клиента

1. Выберите сервер.
2. Введите имя клиента.
3. IP можно оставить пустым — GUI сам выберет свободный адрес из subnet.
4. GUI создаст keypair/PSK, добавит peer в remote config, сделает restart только AWG-контейнера и проверит peer в runtime.

### Disable / Enable клиента

- **Disable** удаляет peer из remote config, но оставляет запись в SQLite и private key.
- **Enable** записывает peer обратно в remote config.

Это удобно для временного отключения устройства без потери config/QR.

### Diagnostics

Кнопка **Diagnose** проверяет:

- SSH и Docker container;
- наличие config file;
- runtime `wg/awg show`;
- host `net.ipv4.ip_forward`;
- NAT/MASQUERADE/SNAT rules;
- наличие subnet mismatch между server subnet и peer allowed IPs;
- количество runtime peers и online peers.

Типичный полезный warning:

```text
handshake/runtime peers exist, but NAT rules do not mention server subnet
```

Это значит, что peer может быть online, но traffic routing/NAT настроены не под ту subnet.

### Backups / Restore

Перед изменением remote config GUI создаёт backup:

```text
/opt/amnezia/awg/wg0.conf.bak-YYYYMMDD-HHMMSS
/opt/amnezia/awg/awg0.conf.bak-YYYYMMDD-HHMMSS
```

В UI можно:

- посмотреть последние backup-файлы;
- восстановить выбранный backup.

Перед restore GUI создаёт backup текущего config и рестартит только соответствующий AWG container.

### Fleet import

Откройте **Fleet import** и вставьте JSON array:

```json
[
  {
    "name": "admrus",
    "host": "127.0.0.1",
    "ssh_user": "root",
    "ssh_key": "/ssh/id_ed25519"
  },
  {
    "name": "admpol",
    "host": "127.0.0.1",
    "ssh_user": "root",
    "ssh_key": "/ssh/id_ed25519"
  }
]
```

Для каждого host GUI:

1. проверит AWG 2.0 и AWG 1.5;
2. добавит найденные серверы;
3. выполнит sync existing peers;
4. покажет summary.

## SQLite, мониторинг и traffic accounting

Основная база:

```text
/data/awg-web-gui.db
```

Таблицы:

| Таблица | Назначение |
| :--- | :--- |
| `servers` | добавленные AWG-серверы и параметры SSH/Docker |
| `clients` | клиенты/peers и данные для генерации config/QR |
| `users` | локальные пользователи GUI |
| `client_stats` | handshake, endpoint, online/offline, RX/TX, total RX/TX |
| `events` | журнал операций и ошибок |
| `settings` | служебные настройки |

Фоновый poller выполняет:

```bash
# AWG 1.5
docker exec amnezia-awg wg show wg0 dump

# AWG 2.0
docker exec amnezia-awg2 awg show awg0 dump
```

Счётчики WireGuard/AWG могут сбрасываться после restart контейнера. GUI хранит runtime counters и накопительные totals; если новый counter меньше предыдущего, это считается reset и новое значение добавляется как delta.

## Security notes

- Пароли пользователей хэшируются через Werkzeug; старый SHA256 admin hash поддерживается для backward compatibility.
- `ssh_password` не отдаётся обратно через `/api/servers`, только флаг `has_ssh_password`.
- `privkey` и `preshared_key` клиента не отдаются через `/api/clients`, только флаги `has_privkey` / `has_preshared_key`.
- Для production используйте HTTPS reverse proxy и смените `admin/admin`.
- SSH password хранится в SQLite, если вы его используете. Для production лучше SSH key.
- Private client keys хранятся в SQLite plain text, чтобы можно было генерировать config/QR. Ограничьте доступ к `/data` и делайте backup аккуратно.

## Проверка

```bash
docker compose ps
docker logs --tail 50 awg-web-gui
curl -I http://127.0.0.1:8095/
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
AWG_ENABLE_POLLER=0 pytest -q
python app.py
```

## Лицензия

MIT
