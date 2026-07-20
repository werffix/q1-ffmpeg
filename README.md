# FFmpeg Metadata Tool

Video metadata editor with web UI. Upload videos, strip/edit metadata via FFmpeg, preview in browser.

## Local run

```bash
docker compose up --build
```

Open `http://localhost:8000`.

## Server deployment

### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and log back in. Verify:

```bash
docker --version
```

### 2. Clone project

```bash
git clone <repo-url> /opt/ffmpeg-metadata
cd /opt/ffmpeg-metadata
```

### 3. Start

```bash
docker compose up -d --build
```

App runs on port `8000` inside the container, mapped to port `8000` on the host.

### 4. Install Caddy

```bash
sudo apt install -y caddy
```

### 5. Configure Caddy

Edit `/etc/caddy/Caddyfile`:

```
yourdomain.com {
    reverse_proxy localhost:8000

    client_max_body_size 2GB
}
```

Replace `yourdomain.com` with your actual domain.

### 6. DNS

Point your domain A record to the server IP:

```
yourdomain.com  ->  A  ->  <server-ip>
```

### 7. Reload Caddy

```bash
sudo systemctl reload caddy
```

HTTPS is automatic via Let's Encrypt.

## Manage

```bash
# Status
docker compose ps

# Logs
docker compose logs -f

# Restart
docker compose restart

# Stop
docker compose down

# Update
git pull && docker compose up -d --build
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload video (multipart) |
| GET | `/api/metadata/{id}` | Read metadata (ffprobe) |
| POST | `/api/metadata/{id}` | Write metadata |
| GET | `/api/stream/{id}` | Stream video |
| GET | `/api/download/{id}` | Download clean file |
| GET | `/api/logs` | Get logs |
