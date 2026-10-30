"""Entry point for both the Windows host PC and the web app on a Linux server: one process runs it all.

  * webhook server + team pages on 127.0.0.1:8787 (reached from the internet only via Cloudflare Tunnel)
  * settings page on 127.0.0.1:8788 (this computer only; on a server also at server.settings_hostname
    through the tunnel, behind Cloudflare Access + the owner password)
  * background ticker + Telegram long polling
  * cloudflared child process when a tunnel token is set

Data lives in %ProgramData%\\HeroezOps on Windows, /data in the Docker image (HEROEZ_HOME overrides).
Saving settings restarts everything in-process; no service restart needed.

Server commands (no .env file: every key is typed into the settings page):
  heroez-ops init            first start: tunnel token + addresses, prints the first owner password
  heroez-ops reset-password  prints a new one-time owner password
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets as pysecrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from . import secrets as secretstore
from .app import create_app
from .config import ConfigError, load_config
from .db import Store
from .docs_ui import ContractsUI
from .service import Service
from .setup_ui import SetupUI

WEBHOOK_PORT, SETUP_PORT = 8787, 8788
log = logging.getLogger("heroez_ops")


def home_dir() -> Path:
    if os.environ.get("HEROEZ_HOME"):
        return Path(os.environ["HEROEZ_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "HeroezOps"
    return Path.home() / ".heroez-ops"


def ensure_home(home: Path, quiet: bool = False) -> None:
    for sub in ("config", "data", "logs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    defaults = Path(__file__).resolve().parent / "defaults"
    for name in ("config.yaml", "holidays.yaml"):
        if not (home / "config" / name).exists():
            shutil.copy(defaults / name, home / "config" / name)
    roster = home / "data" / "talent_roster.csv"
    if not roster.exists():
        roster.write_text("name,phone,handler\n", encoding="utf-8")
    # First start without an owner password: generate one (the Windows installer normally does this).
    if not quiet and not (home / "config" / "owner.json").exists() and not (home / "initial_password.txt").exists():
        _new_password(home)
        print(f"Initial owner password written to {home / 'initial_password.txt'} "
              "(on a server: heroez-ops reset-password prints a new one)")


def setup_logging(home: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(home / "logs" / "heroez.log", maxBytes=5_000_000, backupCount=5,
                                                   encoding="utf-8")
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    # Telegram URLs contain the bot token; keep httpx request logs out of the log file.
    logging.getLogger("httpx").setLevel(logging.WARNING)


class Tunnel:
    """Keeps `cloudflared tunnel run` alive. The token is passed via env, not the command line."""

    def __init__(self, token: str):
        self.token = token
        self.proc: subprocess.Popen | None = None
        self.stop = threading.Event()
        # The installer puts cloudflared.exe next to HeroezOps.exe.
        exe = Path(sys.executable).parent / "cloudflared.exe" if sys.platform == "win32" else None
        self.exe = str(exe) if exe and exe.exists() else shutil.which("cloudflared")

    def status(self) -> str:
        if not self.token:
            return "tidak dipakai (token belum diisi)"
        if not self.exe:
            return "cloudflared tidak ditemukan"
        return "berjalan" if self.proc and self.proc.poll() is None else "berhenti, mencoba lagi"

    def start(self) -> None:
        if self.token and self.exe:
            threading.Thread(target=self._loop, name="tunnel", daemon=True).start()

    def _loop(self) -> None:
        keep = ("PATH", "SystemRoot", "SYSTEMROOT", "windir", "TEMP", "TMP", "ProgramData", "LOCALAPPDATA", "APPDATA",
                "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG")
        env = {k: v for k, v in os.environ.items() if k in keep or k.upper() in keep}   # no API secrets for cloudflared
        env["TUNNEL_TOKEN"] = self.token
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        while not self.stop.is_set():
            log.info("starting cloudflared")
            self.proc = subprocess.Popen([self.exe, "tunnel", "--no-autoupdate", "run"], env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            while self.proc.poll() is None and not self.stop.is_set():
                self.stop.wait(2)
            if not self.stop.is_set():
                log.warning("cloudflared exited (%s); restarting in 10 s", self.proc.returncode)
                self.stop.wait(10)

    def shutdown(self) -> None:
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _bind(port: int) -> socket.socket:
    """Bind exclusively so another local program cannot take or share the port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(128)
    return s


def _serve(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    sock = _bind(port)   # raises OSError if the port is taken: the service must not start half-blind
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name=f"http-{port}", daemon=True)
    t.start()
    return server, t


def run_once(home: Path, stop: threading.Event) -> None:
    """Run until settings are saved (restart) or stop is set."""
    restart = threading.Event()
    sec = secretstore.load(home / "secrets.bin")
    secretstore.apply_to_env(sec)
    store = Store(home / "data" / "heroez.db")
    tunnel = Tunnel(sec.get("CLOUDFLARE_TUNNEL_TOKEN", ""))

    def status() -> dict:
        return {"last_webhook_at": int(store.get_kv("last_webhook_at", "0") or 0),
                "last_tick_at": int(store.get_kv("last_tick_at", "0") or 0), "tunnel": tunnel.status()}

    servers = []
    ui = SetupUI(home, store, restart.set, status=status, port=SETUP_PORT)
    try:
        cfg = load_config(home / "config" / "config.yaml", base_dir=home)
    except ConfigError as e:
        log.error("config problem, only the settings page is running: %s", e)
        cfg = None
    contracts = ContractsUI(home, store, lambda: cfg) if cfg else None
    try:
        servers.append(_serve(ui.create_app(contracts.router(local=True) if contracts else None), SETUP_PORT))
    except OSError as e:
        log.critical("port %d is taken by another program (%s); stopping so the service restarts cleanly", SETUP_PORT, e)
        raise SystemExit(2) from e
    if cfg:
        service = Service(cfg, store, data_dir=home / "data")
        for p in service.engine.load_roster():
            log.warning("roster: %s", p)
        try:
            servers.append(_serve(create_app(cfg, store, service, run_background=True,
                                             extra_routers=(contracts.router(local=False),)), WEBHOOK_PORT))
        except OSError as e:
            log.critical("port %d is taken by another program (%s); stopping so the service restarts cleanly", WEBHOOK_PORT, e)
            raise SystemExit(2) from e
        tunnel.start()   # only after both ports are ours
        log.info("running in %s mode; settings at http://127.0.0.1:%d", cfg.mode, SETUP_PORT)

    while not (restart.is_set() or stop.is_set()):
        restart.wait(1)
    log.info("restarting" if restart.is_set() else "stopping")
    time.sleep(1)  # let the settings page finish its response
    tunnel.shutdown()
    for server, _ in servers:
        server.should_exit = True
    for _, t in servers:
        t.join(15)
    store.conn.close()


def _new_password(home: Path) -> str:
    """A one-time owner password; it becomes the password at the next login and the file is deleted."""
    pw = pysecrets.token_urlsafe(12)
    (home / "initial_password.txt").write_text(pw + "\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(home / "initial_password.txt", 0o600)
    return pw


def cmd_init(home: Path, answers: dict | None = None) -> int:
    """First start on a server: the only things needed before the settings page can be opened."""
    import yaml
    ask = (lambda k, q: answers.get(k, "")) if answers is not None else (lambda k, q: input(q).strip())
    token = ask("token", "Cloudflare Tunnel token (Zero Trust → Networks → Tunnels → install command, after --token): ")
    public = ask("public", "Public address for webhooks and team pages (e.g. ops.heroez.id): ").removeprefix("https://").strip("/")
    settings = ask("settings", "Address for the settings page (e.g. opsadmin.heroez.id): ").removeprefix("https://").strip("/")
    owner = ask("owner", "Owner email(s) for the settings page, comma separated: ")
    if token:
        sec = secretstore.load(home / "secrets.bin")
        sec["CLOUDFLARE_TUNNEL_TOKEN"] = token
        secretstore.save(home / "secrets.bin", sec)
    cfg_path = home / "config" / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    srv = raw.setdefault("server", {})
    if public:
        srv["public_hostname"] = public.lower()
    if settings:
        srv["settings_hostname"] = settings.lower()
    if owner:
        raw.setdefault("access", {})["owner_emails"] = [m.strip().lower() for m in owner.split(",") if "@" in m]
    cfg_path.write_text("# Edited by heroez-ops init.\n" + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if not (home / "config" / "owner.json").exists():
        pw = _new_password(home)
        print(f"\nFirst owner password: {pw}\n(it is used once to log in; change it on the settings page afterwards)")
    print(f"\nDone. Start the app, then open https://{settings or '<settings address>'} in a browser.")
    return 0


def cmd_reset_password(home: Path) -> int:
    (home / "reset_password").write_text("", encoding="utf-8")
    pw = _new_password(home)
    print(f"New one-time owner password: {pw}\nLog in with it at the settings page, then change it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Heroez Ops service")
    ap.add_argument("command", nargs="?", default="run", choices=("run", "init", "reset-password"))
    ap.add_argument("--home", help="data folder (default: %%ProgramData%%\\HeroezOps, /data in Docker)")
    args = ap.parse_args(argv)
    home = Path(args.home) if args.home else home_dir()
    if args.command == "init":
        ensure_home(home, quiet=True)
        return cmd_init(home)
    if args.command == "reset-password":
        ensure_home(home, quiet=True)
        return cmd_reset_password(home)
    ensure_home(home)
    os.chdir(home)  # relative paths in config resolve inside the data folder
    setup_logging(home)
    stop = threading.Event()
    try:
        while not stop.is_set():
            run_once(home, stop)
    except KeyboardInterrupt:  # Windows service stop (WinSW sends Ctrl+C) or Ctrl+C in a console
        stop.set()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
