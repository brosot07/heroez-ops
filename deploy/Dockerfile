# Heroez Ops web app (Linux server). One container runs everything:
# webhooks + team pages, the settings page, the background worker, Telegram polling and the
# Cloudflare Tunnel (cloudflared). No ports are published: all traffic arrives through the tunnel.
FROM python:3.13-slim

# cloudflared, pinned and checksummed (same release as the Windows installer).
ARG CLOUDFLARED_VERSION=2026.10.5
ARG CLOUDFLARED_SHA256=e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298
RUN python - <<PY
import hashlib, os, urllib.request
url = "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
data = urllib.request.urlopen(url, timeout=120).read()
digest = hashlib.sha256(data).hexdigest()
assert digest == "${CLOUDFLARED_SHA256}", f"cloudflared checksum mismatch: {digest}"
open("/usr/local/bin/cloudflared", "wb").write(data)
os.chmod("/usr/local/bin/cloudflared", 0o755)
PY

WORKDIR /app
COPY deploy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY heroez_ops ./heroez_ops
RUN pip install --no-cache-dir --no-deps .

# Runs as an unprivileged user; all data (config, database, secrets, documents, backups) is in /data.
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin heroez \
 && mkdir -p /data && chown heroez:heroez /data && chmod 700 /data
ENV PYTHONUNBUFFERED=1 HEROEZ_HOME=/data TZ=Asia/Jakarta
USER heroez
VOLUME /data
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4).status == 200 else 1)"
CMD ["heroez-ops", "run"]
