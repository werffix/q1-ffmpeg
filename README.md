# FFmpeg Metadata Tool

Редактор метаданных видео с веб-интерфейсом. Загрузка, очистка/редактирование метаданных через FFmpeg, встроенный плеер.

## Развертывание на сервере

### Шаг 1. Подготовка сервера

Установить Docker:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Перелогиниться. Проверить:

```bash
docker --version
```

### Шаг 2. Клонировать проект

```bash
git clone <url-репозитория> /opt/ffmpeg-metadata
cd /opt/ffmpeg-metadata
```

### Шаг 3. Запустить приложение

```bash
docker compose up -d --build
```

Приложение слушает порт `8000`.

### Шаг 4. DNS

В панели управления доменом создать A-запись:

```
video.yourdomain.com  ->  A  ->  <ip-адрес-сервера>
```

Подождать пока запишется (обычно 1-5 минут). Проверить:

```bash
dig video.yourdomain.com +short
```

### Шаг 5. Установить Caddy

```bash
sudo apt install -y caddy
```

### Шаг 6. Настроить Caddy

Отредактировать файл:

```bash
sudo nano /etc/caddy/Caddyfile
```

Заменить всё содержимое на:

```
video.yourdomain.com {
    reverse_proxy localhost:8000

    request_body {
        max_size 2GB
    }

    header {
        X-Frame-Options DENY
        X-Content-Type-Options nosniff
    }
}
```

Заменить `video.yourdomain.com` на свой домен.

Сохранить (Ctrl+O, Enter) и выйти (Ctrl+X).

### Шаг 7. Применить и запустить Caddy

```bash
sudo systemctl reload caddy
```

Готово. Сайт доступен по адресу `https://video.yourdomain.com` на любых устройствах.

Caddy автоматически выдаёт и продлевает SSL-сертификат через Let's Encrypt.

## Управление

```bash
# Статус
docker compose ps

# Логи приложения
docker compose logs -f

# Перезапуск
docker compose restart

# Остановка
docker compose down

# Обновление
git pull && docker compose up -d --build

# Логи Caddy
sudo journalctl -u caddy -f
```

## API

| Метод | Эндпоинт | Описание |
|-------|-----------|----------|
| POST | `/api/upload` | Загрузка видео |
| GET | `/api/metadata/{id}` | Чтение метаданных |
| POST | `/api/metadata/{id}` | Запись метаданных |
| GET | `/api/stream/{id}` | Потоковая передача видео |
| GET | `/api/download/{id}` | Скачивание файла |
| GET | `/api/strip-download/{id}` | Очистка метаданных + скачивание |
| GET | `/api/logs` | Получение логов |
