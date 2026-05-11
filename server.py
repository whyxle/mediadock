from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_ROOT.parent
STATIC_ROOT = APP_ROOT / "static"
CONFIG_PATH = APP_ROOT / "data" / "config.json"
DOWNLOADS_ROOT = APP_ROOT / "downloads"

DEFAULT_PORT = 8765
PO_HTTP_PORT = 4416
PO_HTTP_BASE_URL = f"http://[::1]:{PO_HTTP_PORT}"
MAX_HISTORY = 200
AUDIO_FORMATS = {"mp3", "m4a", "wav", "flac"}
VIDEO_FORMATS = {"mp4", "webm", "best"}
COOKIE_MODES = {"off", "firefox", "chrome", "edge"}
THEMES = {"light", "dark"}

PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%",
    re.IGNORECASE,
)
SPEED_RE = re.compile(r"\s+at\s+(?P<speed>\S+)\s+ETA\s+(?P<eta>\S+)", re.IGNORECASE)
DESTINATION_RE = re.compile(r"(?:Destination|Merging formats into):\s+\"?(?P<path>.+?)\"?$")
ERROR_HINT_RE = re.compile(r"(403|forbidden|sign in|cookies|login)", re.IGNORECASE)
_po_server_process: subprocess.Popen[Any] | None = None
_po_server_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_settings() -> dict[str, Any]:
    deno_path = WORKSPACE_ROOT / "deno" / "deno.exe"
    po_home = WORKSPACE_ROOT / "bgutil-ytdlp-pot-provider" / "server"
    return {
        "outputDir": str(DOWNLOADS_ROOT),
        "concurrency": 2,
        "defaultCookiesMode": "off",
        "pythonPath": sys.executable,
        "ffmpegPath": shutil.which("ffmpeg") or "",
        "denoPath": str(deno_path) if deno_path.exists() else (shutil.which("deno") or ""),
        "poServerHome": str(po_home) if po_home.exists() else "",
        "theme": "light",
    }


def load_settings() -> dict[str, Any]:
    settings = default_settings()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    return normalize_settings(settings)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    base = default_settings()
    base.update(settings or {})
    try:
        base["concurrency"] = max(1, min(4, int(base.get("concurrency", 2))))
    except (TypeError, ValueError):
        base["concurrency"] = 2
    if base.get("defaultCookiesMode") not in COOKIE_MODES:
        base["defaultCookiesMode"] = "off"
    if base.get("theme") not in THEMES:
        base["theme"] = "light"
    base["outputDir"] = str(Path(str(base.get("outputDir") or DOWNLOADS_ROOT)).expanduser())
    for key in ("pythonPath", "ffmpegPath", "denoPath", "poServerHome"):
        base[key] = str(base.get(key) or "")
    return base


def parse_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = "\n".join(str(item) for item in value)
    else:
        raw = str(value or "")
    tokens = re.split(r"[\s,]+", raw)
    urls: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        url = token.strip().strip("<>()[]{}'\"")
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def is_youtube_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def safe_output_dir(path_value: str | None) -> Path:
    path = Path(path_value or DOWNLOADS_ROOT).expanduser()
    if not path.is_absolute():
        path = APP_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def audio_quality_arg(value: str) -> str:
    value = str(value or "best").lower()
    if value == "best":
        return "0"
    if value in {"320", "192", "128"}:
        return f"{value}K"
    return "0"


def video_selector(container: str, height: str) -> str:
    height = str(height or "best").lower()
    cap = "" if height == "best" else f"[height<={height}]"
    if container == "mp4":
        return (
            f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]/"
            f"best{cap}[ext=mp4]/best{cap}"
        )
    if container == "webm":
        return (
            f"bestvideo{cap}[ext=webm]+bestaudio[ext=webm]/"
            f"best{cap}[ext=webm]/best{cap}"
        )
    return f"bestvideo{cap}+bestaudio/best{cap}"


def command_env(settings: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    deno_path = Path(str(settings.get("denoPath") or ""))
    if deno_path.exists():
        env["PATH"] = str(deno_path.parent) + os.pathsep + env.get("PATH", "")
    return env


def add_youtube_args(cmd: list[str], url: str, settings: dict[str, Any]) -> None:
    if not is_youtube_url(url):
        return
    deno_path = str(settings.get("denoPath") or "")
    if deno_path:
        cmd.extend(["--js-runtimes", "deno"])
    cmd.extend(["--extractor-args", "youtube:player_client=mweb"])
    cmd.extend(["--extractor-args", f"youtubepot-bgutilhttp:base_url={PO_HTTP_BASE_URL}"])


def po_http_ping(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{PO_HTTP_BASE_URL}/ping", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("version"))
    except Exception:
        return False


def ensure_po_http_server(settings: dict[str, Any], wait_seconds: float = 20.0) -> None:
    global _po_server_process

    if po_http_ping(timeout=0.8):
        return

    deno_path = Path(str(settings.get("denoPath") or ""))
    po_home = Path(str(settings.get("poServerHome") or ""))
    main_script = po_home / "src" / "main.ts"
    node_modules = po_home / "node_modules"
    cache_dir = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "bgutil-ytdlp-pot-provider"
    if not deno_path.exists() or not main_script.exists() or not node_modules.exists():
        raise RuntimeError("PO-token provider не готов: проверьте пути к Deno и bgutil server.")

    with _po_server_lock:
        if po_http_ping(timeout=0.8):
            return
        if _po_server_process and _po_server_process.poll() is None:
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        read_allow = ",".join(str(path) for path in (cache_dir, node_modules, po_home))
        cmd = [
            str(deno_path),
            "run",
            "--allow-env",
            "--allow-net",
            f"--allow-ffi={node_modules}",
            f"--allow-write={cache_dir}",
            f"--allow-read={read_allow}",
            str(main_script),
            "--port",
            str(PO_HTTP_PORT),
        ]
        _po_server_process = subprocess.Popen(
            cmd,
            cwd=str(po_home),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=command_env(settings),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if po_http_ping(timeout=1.0):
            return
        time.sleep(0.5)
    raise RuntimeError("PO-token HTTP server не успел запуститься.")


def stop_po_http_server() -> None:
    global _po_server_process
    with _po_server_lock:
        if _po_server_process and _po_server_process.poll() is None:
            try:
                _po_server_process.terminate()
            except OSError:
                pass
        _po_server_process = None


def add_cookie_args(cmd: list[str], cookies_mode: str) -> None:
    if cookies_mode in COOKIE_MODES and cookies_mode != "off":
        cmd.extend(["--cookies-from-browser", cookies_mode])


def build_download_command(options: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    url = str(options["url"])
    output_dir = safe_output_dir(options.get("outputDir") or settings.get("outputDir"))
    fmt = str(options.get("format") or "mp3").lower()
    if fmt not in AUDIO_FORMATS | VIDEO_FORMATS:
        fmt = "mp3"

    python_path = str(settings.get("pythonPath") or sys.executable)
    cmd = [
        python_path,
        "-m",
        "yt_dlp",
        "--newline",
        "--no-color",
        "--progress",
        "--print",
        "before_dl:__TITLE__:%(title)s",
        "--print",
        "after_move:filepath",
        "-o",
        str(output_dir / "%(title).200B.%(ext)s"),
    ]

    ffmpeg_path = str(settings.get("ffmpegPath") or "")
    if ffmpeg_path:
        cmd.extend(["--ffmpeg-location", ffmpeg_path])

    playlist_mode = str(options.get("playlistMode") or "single")
    cmd.append("--yes-playlist" if playlist_mode == "playlist" else "--no-playlist")
    add_cookie_args(cmd, str(options.get("cookiesMode") or settings.get("defaultCookiesMode") or "off"))
    add_youtube_args(cmd, url, settings)

    if fmt in AUDIO_FORMATS:
        cmd.extend(
            [
                "-x",
                "--audio-format",
                fmt,
                "--audio-quality",
                audio_quality_arg(str(options.get("audioQuality") or "best")),
            ]
        )
    elif fmt in {"mp4", "webm"}:
        cmd.extend(
            [
                "-f",
                video_selector(fmt, str(options.get("videoHeight") or "best")),
                "--merge-output-format",
                fmt,
            ]
        )
    else:
        cmd.extend(["-f", video_selector("best", str(options.get("videoHeight") or "best"))])

    cmd.append(url)
    return cmd


def build_probe_command(payload: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    url = str(payload["url"])
    python_path = str(settings.get("pythonPath") or sys.executable)
    cmd = [
        python_path,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-color",
        "--no-warnings",
    ]
    if payload.get("playlistMode") == "playlist":
        cmd.extend(["--yes-playlist", "--playlist-end", "25"])
    else:
        cmd.append("--no-playlist")
    add_cookie_args(cmd, str(payload.get("cookiesMode") or settings.get("defaultCookiesMode") or "off"))
    add_youtube_args(cmd, url, settings)
    cmd.append(url)
    return cmd


def parse_progress_line(line: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    match = PROGRESS_RE.search(line)
    if match:
        result["progress"] = float(match.group("percent"))
        speed_match = SPEED_RE.search(line)
        if speed_match:
            result["speed"] = speed_match.group("speed").strip()
            result["eta"] = speed_match.group("eta").strip()
    destination = DESTINATION_RE.search(line)
    if destination:
        result["filePath"] = destination.group("path").strip()
    stripped = line.strip()
    if re.match(r"^[A-Za-z]:\\", stripped) or stripped.startswith("/"):
        result["filePath"] = stripped
    return result


def find_recent_output(output_dir: str, started_at: float) -> str:
    try:
        files = [p for p in Path(output_dir).glob("*") if p.is_file() and p.stat().st_mtime >= started_at - 2]
    except OSError:
        return ""
    if not files:
        return ""
    return str(max(files, key=lambda p: p.stat().st_mtime))


@dataclass
class Job:
    id: str
    url: str
    format: str
    audioQuality: str
    videoHeight: str
    playlistMode: str
    outputDir: str
    cookiesMode: str
    status: str = "queued"
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    title: str = ""
    filePath: str = ""
    error: str = ""
    canRetryWithCookies: bool = False
    createdAt: str = field(default_factory=now_iso)
    startedAt: str = ""
    finishedAt: str = ""
    lastLog: list[str] = field(default_factory=list)
    process: subprocess.Popen[str] | None = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "format": self.format,
            "audioQuality": self.audioQuality,
            "videoHeight": self.videoHeight,
            "playlistMode": self.playlistMode,
            "outputDir": self.outputDir,
            "cookiesMode": self.cookiesMode,
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "title": self.title,
            "filePath": self.filePath,
            "error": self.error,
            "canRetryWithCookies": self.canRetryWithCookies,
            "createdAt": self.createdAt,
            "startedAt": self.startedAt,
            "finishedAt": self.finishedAt,
            "lastLog": self.lastLog[-8:],
        }


class DownloadManager:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = normalize_settings(settings)
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.pending: deque[str] = deque()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.active_count = 0
        self.stopping = False
        self.scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler.start()

    def update_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        with self.condition:
            self.settings = normalize_settings(settings)
            self.condition.notify_all()
            return dict(self.settings)

    def add_jobs(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        urls = parse_urls(payload.get("urls"))
        if not urls:
            raise ValueError("Добавьте хотя бы одну ссылку.")
        output_dir = str(safe_output_dir(payload.get("outputDir") or self.settings.get("outputDir")))
        created: list[Job] = []
        with self.condition:
            for url in urls:
                job = Job(
                    id=uuid.uuid4().hex[:12],
                    url=url,
                    format=str(payload.get("format") or "mp3"),
                    audioQuality=str(payload.get("audioQuality") or "best"),
                    videoHeight=str(payload.get("videoHeight") or "best"),
                    playlistMode=str(payload.get("playlistMode") or "single"),
                    outputDir=output_dir,
                    cookiesMode=str(payload.get("cookiesMode") or self.settings.get("defaultCookiesMode") or "off"),
                )
                self.jobs[job.id] = job
                self.pending.append(job.id)
                created.append(job)
            while len(self.jobs) > MAX_HISTORY:
                self.jobs.popitem(last=False)
            self.condition.notify_all()
        return [job.public() for job in created]

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [job.public() for job in reversed(self.jobs.values())]

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.condition:
            job = self._get_job(job_id)
            if job.status == "queued":
                self.pending = deque(item for item in self.pending if item != job_id)
                job.status = "cancelled"
                job.finishedAt = now_iso()
                self.condition.notify_all()
                return job.public()
            if job.status == "running" and job.process:
                job.status = "cancelling"
                try:
                    job.process.terminate()
                except OSError:
                    pass
                return job.public()
            return job.public()

    def retry(self, job_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        overrides = overrides or {}
        with self.lock:
            old = self._get_job(job_id)
            payload = {
                "urls": [old.url],
                "format": overrides.get("format", old.format),
                "audioQuality": overrides.get("audioQuality", old.audioQuality),
                "videoHeight": overrides.get("videoHeight", old.videoHeight),
                "playlistMode": overrides.get("playlistMode", old.playlistMode),
                "outputDir": overrides.get("outputDir", old.outputDir),
                "cookiesMode": overrides.get("cookiesMode", old.cookiesMode),
            }
        return self.add_jobs(payload)[0]

    def open_target(self, job_id: str, target: str) -> dict[str, Any]:
        with self.lock:
            job = self._get_job(job_id)
            path = Path(job.filePath) if target == "file" and job.filePath else Path(job.outputDir)
            if target == "folder" and job.filePath:
                path = Path(job.filePath).parent
        if not path.exists():
            raise FileNotFoundError(str(path))
        open_local_path(path)
        return {"ok": True, "path": str(path)}

    def shutdown(self) -> None:
        with self.condition:
            self.stopping = True
            for job in self.jobs.values():
                if job.status in {"running", "cancelling"} and job.process:
                    try:
                        job.process.terminate()
                    except OSError:
                        pass
            self.condition.notify_all()

    def _get_job(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise KeyError("Задача не найдена.") from exc

    def _scheduler_loop(self) -> None:
        while True:
            with self.condition:
                while not self.stopping and (
                    not self.pending or self.active_count >= int(self.settings.get("concurrency", 2))
                ):
                    self.condition.wait(timeout=1)
                if self.stopping:
                    return
                job_id = self.pending.popleft()
                job = self.jobs[job_id]
                job.status = "running"
                job.startedAt = now_iso()
                self.active_count += 1
            threading.Thread(target=self._run_job, args=(job_id,), daemon=True).start()

    def _run_job(self, job_id: str) -> None:
        started_monotonic = time.time()
        with self.lock:
            job = self.jobs[job_id]
            settings = dict(self.settings)
            options = {
                "url": job.url,
                "format": job.format,
                "audioQuality": job.audioQuality,
                "videoHeight": job.videoHeight,
                "playlistMode": job.playlistMode,
                "outputDir": job.outputDir,
                "cookiesMode": job.cookiesMode,
            }
        try:
            if is_youtube_url(job.url):
                ensure_po_http_server(settings)
            cmd = build_download_command(options, settings)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                cmd,
                cwd=str(APP_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=command_env(settings),
                creationflags=creationflags,
            )
            with self.lock:
                job = self.jobs[job_id]
                job.process = process
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                self._handle_output(job_id, line)
            return_code = process.wait()
            with self.condition:
                job = self.jobs[job_id]
                job.process = None
                if job.status == "cancelling":
                    job.status = "cancelled"
                    job.error = ""
                elif return_code == 0:
                    job.status = "completed"
                    job.progress = 100.0
                    if not job.filePath:
                        job.filePath = find_recent_output(job.outputDir, started_monotonic)
                else:
                    job.status = "failed"
                    job.error = job.error or "yt-dlp завершился с ошибкой."
                    job.canRetryWithCookies = bool(ERROR_HINT_RE.search(job.error + "\n" + "\n".join(job.lastLog)))
                job.finishedAt = now_iso()
                self.active_count -= 1
                self.condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - surfaced to UI as job error
            with self.condition:
                job = self.jobs[job_id]
                job.process = None
                if job.status != "cancelled":
                    job.status = "failed"
                    job.error = str(exc)
                    job.canRetryWithCookies = bool(ERROR_HINT_RE.search(job.error))
                    job.finishedAt = now_iso()
                self.active_count -= 1
                self.condition.notify_all()

    def _handle_output(self, job_id: str, line: str) -> None:
        update = parse_progress_line(line)
        with self.lock:
            job = self.jobs[job_id]
            if line:
                job.lastLog.append(line)
                job.lastLog = job.lastLog[-30:]
            if line.startswith("__TITLE__:"):
                job.title = line.split(":", 1)[-1].strip()
            if "Downloading webpage" in line and not job.title:
                job.title = urllib.parse.urlparse(job.url).netloc
            if "Extracting URL:" in line:
                job.title = line.split("Extracting URL:", 1)[-1].strip()
            if "ERROR:" in line:
                job.error = line.split("ERROR:", 1)[-1].strip()
            if "progress" in update:
                job.progress = max(job.progress, float(update["progress"]))
            if "speed" in update:
                job.speed = str(update["speed"])
            if "eta" in update:
                job.eta = str(update["eta"])
            if "filePath" in update:
                path = update["filePath"]
                if path:
                    job.filePath = str(Path(path).expanduser())


def open_local_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class AppState:
    def __init__(self) -> None:
        self.settings = load_settings()
        safe_output_dir(self.settings["outputDir"])
        self.manager = DownloadManager(self.settings)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.settings)
        merged.update(payload or {})
        self.settings = save_settings(merged)
        return self.manager.update_settings(self.settings)


def run_checked(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 8) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output.splitlines()[0] if output else ""


def health(settings: dict[str, Any]) -> dict[str, Any]:
    python_path = str(settings.get("pythonPath") or sys.executable)
    ffmpeg_path = str(settings.get("ffmpegPath") or "ffmpeg")
    deno_path = str(settings.get("denoPath") or "deno")
    po_home = Path(str(settings.get("poServerHome") or ""))
    env = command_env(settings)
    yt_ok, yt_version = run_checked([python_path, "-m", "yt_dlp", "--version"], env=env)
    ff_ok, ff_version = run_checked([ffmpeg_path, "-version"], env=env)
    deno_ok, deno_version = run_checked([deno_path, "--version"], env=env)
    po_script = po_home / "src" / "generate_once.ts"
    po_http_ok = po_http_ping(timeout=0.8)
    po_ok = po_script.exists()
    return {
        "ok": all([yt_ok, ff_ok, deno_ok, po_ok]),
        "tools": {
            "ytDlp": {"ok": yt_ok, "version": yt_version, "path": python_path},
            "ffmpeg": {"ok": ff_ok, "version": ff_version, "path": ffmpeg_path},
            "deno": {"ok": deno_ok, "version": deno_version, "path": deno_path},
            "poProvider": {"ok": po_ok, "http": po_http_ok, "path": str(po_home)},
        },
        "settings": settings,
    }


def probe(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    urls = parse_urls(payload.get("url"))
    if not urls:
        raise ValueError("Введите корректную ссылку.")
    payload = dict(payload)
    payload["url"] = urls[0]
    if is_youtube_url(urls[0]):
        ensure_po_http_server(settings)
    cmd = build_probe_command(payload, settings)
    completed = subprocess.run(
        cmd,
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=command_env(settings),
        timeout=75,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Не удалось получить информацию.").strip()
        raise RuntimeError(message[-1200:])
    info = json.loads(completed.stdout)
    formats = []
    for item in info.get("formats") or []:
        formats.append(
            {
                "formatId": item.get("format_id"),
                "ext": item.get("ext"),
                "resolution": item.get("resolution") or item.get("format_note"),
                "height": item.get("height"),
                "fps": item.get("fps"),
                "vcodec": item.get("vcodec"),
                "acodec": item.get("acodec"),
                "tbr": item.get("tbr"),
                "filesize": item.get("filesize") or item.get("filesize_approx"),
            }
        )
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "webpageUrl": info.get("webpage_url") or urls[0],
        "playlistCount": len(info.get("entries") or []),
        "formats": formats[:120],
    }


class AppHandler(BaseHTTPRequestHandler):
    state: AppState
    server_version = "LocalVideoDownloader/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{now_iso()}] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                self._send_file(STATIC_ROOT / "index.html")
            elif path.startswith("/static/"):
                self._send_file(STATIC_ROOT / path.removeprefix("/static/"))
            elif path == "/api/health":
                self._json(health(self.state.settings))
            elif path == "/api/jobs":
                self._json({"jobs": self.state.manager.list_jobs()})
            elif path == "/api/settings":
                self._json({"settings": self.state.settings})
            else:
                self._json({"error": "Не найдено."}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/probe":
                self._json({"info": probe(payload, self.state.settings)})
            elif path == "/api/jobs":
                self._json({"jobs": self.state.manager.add_jobs(payload)}, 201)
            elif path == "/api/settings":
                self._json({"settings": self.state.update_settings(payload)})
            elif path.startswith("/api/jobs/"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 4:
                    self._json({"error": "Некорректный запрос."}, 400)
                    return
                job_id, action = parts[2], parts[3]
                if action == "cancel":
                    self._json({"job": self.state.manager.cancel(job_id)})
                elif action == "retry":
                    self._json({"job": self.state.manager.retry(job_id, payload)}, 201)
                elif action == "open-file":
                    self._json(self.state.manager.open_target(job_id, "file"))
                elif action == "open-folder":
                    self._json(self.state.manager.open_target(job_id, "folder"))
                else:
                    self._json({"error": "Неизвестное действие."}, 404)
            else:
                self._json({"error": "Не найдено."}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except KeyError as exc:
            self._json({"error": str(exc).strip("'")}, 404)
        except FileNotFoundError as exc:
            self._json({"error": f"Файл не найден: {exc}"}, 404)
        except subprocess.TimeoutExpired:
            self._json({"error": "Запрос занял слишком много времени."}, 504)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_ROOT.resolve())) or not resolved.exists() or not resolved.is_file():
            self._json({"error": "Файл не найден."}, 404)
            return
        body = resolved.read_bytes()
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run(host: str, port: int) -> None:
    DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    AppHandler.state = AppState()
    httpd = ThreadingHTTPServer((host, port), AppHandler)
    url = f"http://{host}:{httpd.server_port}"
    print(f"Local Video Downloader запущен: {url}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        AppHandler.state.manager.shutdown()
        stop_po_http_server()
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Video Downloader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
