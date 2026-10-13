"""Optional cloud persistence for hosts with an ephemeral disk (Render free, Koyeb, containers...).

Quickz keeps SQLite as its hot path (a game must never wait on a network round trip). When an S3-compatible
bucket is configured (Cloudflare R2, Backblaze B2, AWS S3, MinIO...) it
  * restores the database from the latest snapshot on boot, if the local disk is empty,
  * snapshots the database a few seconds after anything changes (SQLite online-backup API, gzip),
  * copies uploaded images/audio/video to the bucket and fetches them back on demand.

Pure standard library (SigV4 signing included) so there is nothing extra to install.

    QUICKZ_S3_ENDPOINT   https://<account>.r2.cloudflarestorage.com   (or https://s3.<region>.backblazeb2.com)
    QUICKZ_S3_BUCKET     my-quickz-bucket
    QUICKZ_S3_KEY_ID / QUICKZ_S3_SECRET
    QUICKZ_S3_REGION     auto (R2)  |  us-west-004 (B2 - must match the endpoint)
    QUICKZ_S3_PREFIX     quickz     (folder inside the bucket)
    QUICKZ_BACKUP_INTERVAL  10      (seconds between checks)
"""

import asyncio
import datetime as dt
import gzip
import hashlib
import hmac
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

log = logging.getLogger("quickz.cloud")


class CloudError(RuntimeError):
    pass


# ---------------------------------------------------------------- AWS Signature V4
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(method: str, host: str, path: str, headers: dict, payload_hash: str, key_id: str, secret: str,
            region: str, amz_date: str, query: str = "", service: str = "s3") -> dict:
    """Returns the headers to send (Authorization, x-amz-date, x-amz-content-sha256, + the ones given)."""
    hdrs = {k.lower(): str(v).strip() for k, v in headers.items()}
    hdrs.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    names = sorted(hdrs)
    canonical_headers = "".join(f"{k}:{hdrs[k]}\n" for k in names)
    signed = ";".join(names)
    canonical = "\n".join([method, quote(path, safe="/-_.~"), query, canonical_headers, signed, payload_hash])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret).encode(), amz_date[:8])
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return hdrs


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    key_id: str
    secret: str
    region: str = "auto"
    prefix: str = "quickz"


class S3Client:
    """Minimal path-style S3 client: PUT and GET of whole objects."""

    def __init__(self, cfg: S3Config, timeout: float = 20.0):
        self.cfg, self.timeout = cfg, timeout
        self.host = urlparse(cfg.endpoint).netloc

    def _call(self, method: str, key: str, data: bytes = b"", content_type: str = "") -> tuple[int, bytes]:
        path = f"/{self.cfg.bucket}/{key}"
        payload_hash = hashlib.sha256(data).hexdigest()
        amz_date = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        extra = {"content-type": content_type} if content_type else {}
        headers = sign_v4(method, self.host, path, extra, payload_hash, self.cfg.key_id, self.cfg.secret, self.cfg.region, amz_date)
        headers.pop("host", None)  # urllib sets it
        req = urllib.request.Request(self.cfg.endpoint.rstrip("/") + quote(path, safe="/-_.~"),
                                     data=data if method == "PUT" else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise CloudError(f"cannot reach the bucket: {e}") from e

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        status, body = self._call("PUT", key, data, content_type)
        if status not in (200, 201, 204):
            raise CloudError(f"PUT {key} -> HTTP {status} {body[:200]!r}")

    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None if it doesn't exist. Any other failure raises."""
        status, body = self._call("GET", key)
        if status == 200:
            return body
        if status == 404 or (status == 403 and b"NoSuchKey" in body):
            return None
        raise CloudError(f"GET {key} -> HTTP {status} {body[:200]!r}")


# ---------------------------------------------------------------- the sync service
class CloudSync:
    def __init__(self, s3: S3Client, interval: float = 10.0):
        self.s3, self.interval = s3, max(2.0, interval)
        self.prefix = s3.cfg.prefix.strip("/")
        self.db = None
        self.uploads_dir: Path | None = None
        self._last_changes: int | None = None
        self._task: asyncio.Task | None = None
        self._lock = threading.Lock()
        self.status = {"enabled": True, "last_ok": None, "last_error": None, "snapshots": 0, "restored": False,
                       "bucket": s3.cfg.bucket, "endpoint": s3.host}

    @classmethod
    def from_env(cls):
        env = os.environ
        need = ("QUICKZ_S3_ENDPOINT", "QUICKZ_S3_BUCKET", "QUICKZ_S3_KEY_ID", "QUICKZ_S3_SECRET")
        if not all(env.get(k) for k in need):
            return None
        cfg = S3Config(env["QUICKZ_S3_ENDPOINT"].rstrip("/"), env["QUICKZ_S3_BUCKET"], env["QUICKZ_S3_KEY_ID"], env["QUICKZ_S3_SECRET"],
                       env.get("QUICKZ_S3_REGION", "auto"), env.get("QUICKZ_S3_PREFIX", "quickz"))
        return cls(S3Client(cfg), float(env.get("QUICKZ_BACKUP_INTERVAL", "10")))

    # ---- keys
    @property
    def db_key(self) -> str:
        return f"{self.prefix}/db/quickz.db.gz"

    def upload_key(self, name: str) -> str:
        return f"{self.prefix}/uploads/{name}"

    # ---- boot
    def restore_if_needed(self, db_path: Path) -> str:
        """'kept' (local DB exists), 'restored', or 'fresh' (bucket is reachable and has no snapshot yet).
        If the bucket can't be read we refuse to continue: starting empty would let the next snapshot overwrite
        good data. The platform will retry the start."""
        if db_path.exists():
            return "kept"
        try:
            blob = self.s3.get(self.db_key)
        except CloudError as e:
            self.status["last_error"] = str(e)
            raise CloudError(f"Cloud backup is configured but the snapshot could not be read ({e}). "
                             "Refusing to start with an empty database; check the QUICKZ_S3_* settings.") from e
        if blob is None:
            log.warning("cloud backup: no snapshot in the bucket yet - starting fresh")
            return "fresh"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(gzip.decompress(blob))
        self.status["restored"] = True
        log.warning("cloud backup: database restored from the bucket (%d bytes)", db_path.stat().st_size)
        return "restored"

    def attach(self, db, uploads_dir: Path):
        self.db, self.uploads_dir = db, uploads_dir

    # ---- snapshots
    def snapshot_now(self, force: bool = False) -> bool:
        """Upload a consistent snapshot if the database changed. Safe to call from any thread."""
        with self._lock:
            changes = self.db.change_count()
            if not force and changes == self._last_changes:
                return False
            try:
                blob = gzip.compress(self.db.snapshot_bytes(), compresslevel=6)
                self.s3.put(self.db_key, blob, "application/gzip")
                hour = time.strftime("%Y%m%d%H", time.gmtime())
                self.s3.put(f"{self.prefix}/db/hourly/{hour}.db.gz", blob, "application/gzip")  # cheap history
            except Exception as e:  # network blips must never hurt a running game
                self.status["last_error"] = str(e)
                log.warning("cloud backup failed (will retry): %s", e)
                return False
            self._last_changes = changes
            self.status.update(last_ok=time.time(), last_error=None, snapshots=self.status["snapshots"] + 1, last_bytes=len(blob))
            return True

    async def run(self):
        while True:
            await asyncio.sleep(self.interval)
            try:
                await asyncio.to_thread(self.snapshot_now)
            except Exception:  # pragma: no cover - defensive
                log.exception("cloud backup loop error")

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        await asyncio.to_thread(self.snapshot_now)  # final flush on a clean shutdown

    # ---- uploads
    def push_upload(self, path: Path):
        """Copy a freshly uploaded file to the bucket in the background."""
        def work():
            try:
                self.s3.put(self.upload_key(path.name), path.read_bytes(), "application/octet-stream")
            except Exception as e:
                self.status["last_error"] = str(e)
                log.warning("cloud upload of %s failed: %s", path.name, e)
        threading.Thread(target=work, daemon=True).start()

    def fetch_upload(self, name: str) -> bytes | None:
        try:
            return self.s3.get(self.upload_key(name))
        except CloudError as e:
            log.warning("cloud fetch of %s failed: %s", name, e)
            return None
