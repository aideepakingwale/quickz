import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


def lan_ip() -> str:
    """Best-effort LAN address of this machine (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: Path("data").resolve())
    public_url: str = ""
    # Optional fixed token for the very first Super Admin setup (scripted installs). Normally generated per start.
    setup_token: str = ""
    session_days: int = 7
    max_media_mb: int = 50     # audio/video upload cap; images are always capped at 5 MB
    # Behind a reverse proxy / PaaS router: trust X-Forwarded-* so https links, cookies and per-IP limits are right.
    trust_proxy: bool = False

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if not self.public_url:
            self.public_url = f"http://{lan_ip()}:{self.port}"
        self.public_url = self.public_url.rstrip("/")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "quickz.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def load_settings() -> Settings:
    kwargs = {}
    if os.environ.get("QUICKZ_HOST"):
        kwargs["host"] = os.environ["QUICKZ_HOST"]
    # QUICKZ_PORT wins; otherwise the PORT variable that Render, Koyeb, Railway and Heroku-style hosts inject
    port = os.environ.get("QUICKZ_PORT") or os.environ.get("PORT")
    if port:
        kwargs["port"] = int(port)
    if os.environ.get("QUICKZ_DATA"):
        kwargs["data_dir"] = Path(os.environ["QUICKZ_DATA"]).resolve()
    public = os.environ.get("QUICKZ_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")  # Render sets the latter
    if public:
        kwargs["public_url"] = public
    on_platform = bool(os.environ.get("RENDER") or os.environ.get("KOYEB_APP_NAME") or os.environ.get("DYNO"))
    trust = os.environ.get("QUICKZ_TRUST_PROXY")
    kwargs["trust_proxy"] = (trust.lower() in ("1", "true", "yes", "on")) if trust else on_platform
    if os.environ.get("QUICKZ_SETUP_TOKEN"):
        kwargs["setup_token"] = os.environ["QUICKZ_SETUP_TOKEN"]
    if os.environ.get("QUICKZ_MAX_MEDIA_MB"):
        kwargs["max_media_mb"] = max(1, int(os.environ["QUICKZ_MAX_MEDIA_MB"]))
    if os.environ.get("QUICKZ_SESSION_DAYS"):
        kwargs["session_days"] = max(1, int(os.environ["QUICKZ_SESSION_DAYS"]))
    return Settings(**kwargs)
