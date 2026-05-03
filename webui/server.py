from __future__ import annotations

import asyncio
import secrets
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES_FILE = PROJECT_ROOT / "config" / "categories.yaml"
ENV_FILE = Path(os.getenv("YMF_ENV_FILE", "/app/.secrets/.env" if Path("/app").exists() else str(PROJECT_ROOT / ".env")))
SCHEDULES_FILE = Path(
    os.getenv("YMF_SCHEDULES_FILE", "/app/.secrets/schedules.json" if Path("/app").exists() else str(PROJECT_ROOT / ".secrets" / "schedules.json"))
)
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SCHEDULE_UNITS = {"minutes": 60, "hours": 3600, "days": 86400}
DEFAULT_SCHEDULE_TIMEZONE = "Europe/Madrid"
_scheduler_task: asyncio.Task | None = None
_running_schedule_ids: set[str] = set()

SETTING_KEYS = [
    "ELEVENLABS_API_KEY",
    "MUBERT_CUSTOMER_ID",
    "MUBERT_ACCESS_TOKEN",
    "GEMINI_API_KEY",
    "GEMINI_MUSIC_MODEL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_TEXT_MODEL",
    "YOUTUBE_CLIENT_SECRETS",
    "YOUTUBE_TOKEN_FILE",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REDIRECT_URI",
    "YMF_WORKDIR",
    "CHANNEL_THEME",
    "CHANNEL_AESTHETIC",
    "CHANNEL_VISUAL_STYLE",
    "CHANNEL_COLOR_PALETTE",
    "CHANNEL_SONIC_IDENTITY",
    "CHANNEL_AVOID",
]

app = FastAPI(title="YT Music Factory UI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ymf_cmd() -> list[str]:
    if shutil.which("ymf"):
        return ["ymf"]
    return [sys.executable, "-m", "yt_music_factory"]


def _load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def _merged_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_load_env())
    return env


def _write_env(updates: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    )
    written: set[str] = set()
    result_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result_lines.append(line)
        elif "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                result_lines.append(f"{key}={updates[key]}")
                written.add(key)
            else:
                result_lines.append(line)

    for key, value in updates.items():
        if key not in written:
            result_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(result_lines) + "\n", encoding="utf-8")


def _get_workdir() -> Path:
    env = _load_env()
    workdir = env.get("YMF_WORKDIR") or os.getenv("YMF_WORKDIR") or "./runs"
    path = Path(workdir)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _latest_mtime(path: Path) -> float:
    try:
        latest = path.stat().st_mtime
    except OSError:
        return 0
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            pass
    return latest


def _first_existing(paths: list[Path]) -> str | None:
    for path in paths:
        if path.exists():
            return str(path)
    return None


def _run_snapshot(run_dir: Path) -> dict | None:
    result_file = run_dir / "result.json"
    data: dict = {}
    created_at = _latest_mtime(run_dir)

    if result_file.exists():
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            created_at = result_file.stat().st_mtime
        except Exception:
            data = {}

    slug = run_dir.name
    render_dir = run_dir / "render"
    inferred = {
        "job_dir": str(run_dir),
        "metadata_path": _first_existing([run_dir / "youtube_metadata.json"]),
        "final_audio": _first_existing([render_dir / f"{slug}.m4a"]),
        "final_video": _first_existing([render_dir / f"{slug}.mp4"]),
        "thumbnail": _first_existing([render_dir / f"{slug}_thumb.jpg"]),
    }
    inferred["audio_files"] = data.get("audio_files") or [
        str(path) for path in sorted((run_dir / "audio").glob("*")) if path.is_file()
    ]
    inferred["image_files"] = data.get("image_files") or [
        str(path) for path in sorted((run_dir / "images").glob("*")) if path.is_file()
    ]

    snapshot = {**inferred, **data}
    if not any(snapshot.get(key) for key in ("final_video", "final_audio", "thumbnail", "metadata_path")):
        return None
    return {"slug": slug, "created_at": created_at, **snapshot}


def _path_from_env(env: dict[str, str], key: str, default: str) -> Path:
    path = Path(env.get(key) or default)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _youtube_token_path(env: dict[str, str]) -> Path:
    return _path_from_env(env, "YOUTUBE_TOKEN_FILE", "/app/.secrets/youtube-token.json")


def _oauth_state_path(env: dict[str, str]) -> Path:
    return _youtube_token_path(env).with_name("youtube-oauth-state.json")


def _external_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def _youtube_redirect_uri(request: Request, env: dict[str, str]) -> str:
    return env.get("YOUTUBE_REDIRECT_URI") or f"{_external_base_url(request)}/api/youtube/oauth/callback"


def _youtube_client_config(env: dict[str, str], redirect_uri: str) -> dict | None:
    client_id = env.get("YOUTUBE_CLIENT_ID")
    client_secret = env.get("YOUTUBE_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "project_id": env.get("YOUTUBE_PROJECT_ID", ""),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": [redirect_uri],
            }
        }

    secrets_path = _path_from_env(env, "YOUTUBE_CLIENT_SECRETS", "/app/.secrets/client_secret.json")
    if not secrets_path.exists():
        return None
    payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    if "web" in payload:
        payload["web"]["redirect_uris"] = list(set(payload["web"].get("redirect_uris", []) + [redirect_uri]))
        return payload
    if "installed" in payload:
        installed = payload["installed"]
        return {
            "web": {
                "client_id": installed.get("client_id"),
                "project_id": installed.get("project_id", ""),
                "auth_uri": installed.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": installed.get("token_uri", "https://oauth2.googleapis.com/token"),
                "auth_provider_x509_cert_url": installed.get(
                    "auth_provider_x509_cert_url", "https://www.googleapis.com/oauth2/v1/certs"
                ),
                "client_secret": installed.get("client_secret"),
                "redirect_uris": [redirect_uri],
            }
        }
    return None


def _load_categories() -> dict:
    if not CATEGORIES_FILE.exists():
        return {}
    with open(CATEGORIES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_schedules() -> list[dict]:
    if not SCHEDULES_FILE.exists():
        return []
    try:
        payload = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _save_schedules(schedules: list[dict]) -> None:
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _interval_seconds(schedule: dict) -> int:
    value = float(schedule.get("frequency_value") or 24)
    unit = str(schedule.get("frequency_unit") or "hours")
    return max(60, int(value * SCHEDULE_UNITS.get(unit, 3600)))


def _schedule_timezone() -> ZoneInfo:
    name = os.getenv("YMF_SCHEDULE_TIMEZONE", DEFAULT_SCHEDULE_TIMEZONE)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _parse_run_time(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value).strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _next_run_at(schedule: dict, *, from_ts: float | None = None) -> float:
    now = time.time() if from_ts is None else from_ts
    run_time = _parse_run_time(schedule.get("run_time"))
    if run_time and schedule.get("frequency_unit") == "days":
        every_days = max(1, int(float(schedule.get("frequency_value") or 1)))
        tz = _schedule_timezone()
        now_dt = datetime.fromtimestamp(now, tz)
        hour, minute = run_time
        candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_dt:
            candidate += timedelta(days=every_days)
        return candidate.timestamp()
    return now + _interval_seconds(schedule)


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\s-]", "", value or "").lower()
    value = re.sub(r"\s+", "-", value).strip("-")
    value = re.sub(r"-+", "-", value)
    return value[:50] or "auto-video"


def _channel_style_from_env(env: dict[str, str]) -> dict[str, str]:
    return {
        "theme": env.get("CHANNEL_THEME", ""),
        "aesthetic": env.get("CHANNEL_AESTHETIC", ""),
        "visual_style": env.get("CHANNEL_VISUAL_STYLE", ""),
        "color_palette": env.get("CHANNEL_COLOR_PALETTE", ""),
        "sonic_identity": env.get("CHANNEL_SONIC_IDENTITY", ""),
        "avoid": env.get("CHANNEL_AVOID", ""),
    }


def _schedule_spec_yaml(schedule: dict) -> tuple[str, str]:
    categories = _load_categories()
    category_key = str(schedule.get("category_key") or "focus_lofi")
    category = categories.get(category_key, {})
    now = int(time.time())
    suffix = secrets.token_hex(3)
    title_seed = str(schedule.get("title_seed") or category.get("label") or "automatic music mix")
    slug = f"{_safe_slug(title_seed)}-{now}-{suffix}"
    env = _merged_env()
    upload = bool(schedule.get("upload", True))
    spec = {
        "job": {
            "slug": slug,
            "title_seed": title_seed,
            "language": "en",
            "target_minutes": float(schedule.get("target_minutes") or 60),
        },
        "category_key": category_key,
        "channel_style": _channel_style_from_env(env),
        "music": {
            "provider": schedule.get("music_provider") or ("lyria" if env.get("GEMINI_API_KEY") else "local"),
            "prompt": category.get("music_prompt", ""),
            "track_count": int(schedule.get("track_count") or 4),
            "track_duration_seconds": int(schedule.get("track_duration") or 180),
            "instrumental": True,
            "output_format": "wav",
        },
        "images": {
            "provider": schedule.get("images_provider") or ("gemini" if env.get("GEMINI_API_KEY") else "local"),
            "prompt": category.get("image_prompt", ""),
            "count": int(schedule.get("images_count") or 1),
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
        "video": {
            "resolution": "1920x1080",
            "fps": 1,
            "visual_mode": "slideshow",
            "image_duration_seconds": 60,
            "video_preset": "ultrafast",
            "audio_bitrate": "128k",
            "normalize_audio": False,
        },
        "seo": {
            "provider": schedule.get("seo_provider") or ("gemini" if env.get("GEMINI_API_KEY") else "local"),
            "primary_keyword": schedule.get("seo_keyword") or (category.get("primary_keywords") or [""])[0],
        },
        "youtube": {
            "upload": upload,
            "privacy_status": schedule.get("privacy_status") or "private",
            "contains_synthetic_media": True,
            "made_for_kids": False,
            "notify_subscribers": False,
            "set_thumbnail": True,
        },
    }
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), slug


def _update_schedule(schedule_id: str, updates: dict) -> dict | None:
    schedules = _load_schedules()
    updated = None
    for schedule in schedules:
        if schedule.get("id") == schedule_id:
            schedule.update(updates)
            updated = schedule
            break
    _save_schedules(schedules)
    return updated


async def _run_schedule(schedule: dict) -> None:
    schedule_id = str(schedule.get("id"))
    if schedule_id in _running_schedule_ids:
        return
    _running_schedule_ids.add(schedule_id)
    spec_yaml, slug = _schedule_spec_yaml(schedule)
    _update_schedule(
        schedule_id,
        {
            "last_status": "running",
            "last_started_at": time.time(),
            "last_slug": slug,
        },
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(spec_yaml)
        tmp_path = f.name

    try:
        upload = bool(schedule.get("upload", True))
        flag = "--upload" if upload else "--no-upload"
        cmd = _ymf_cmd() + ["render", tmp_path, "--workdir", str(_get_workdir()), flag]
        print(f"[scheduler] Starting '{schedule.get('name') or schedule_id}' as {slug}: {' '.join(cmd)}", flush=True)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=_merged_env(),
        )
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            print(f"[schedule:{schedule_id}] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
        await process.wait()
        status = "success" if process.returncode == 0 else "error"
        _update_schedule(
            schedule_id,
            {
                "last_status": status,
                "last_returncode": process.returncode,
                "last_finished_at": time.time(),
                "next_run_at": _next_run_at(schedule),
                "last_slug": slug,
            },
        )
        print(f"[scheduler] Finished '{schedule.get('name') or schedule_id}' status={status}", flush=True)
    except Exception as exc:
        _update_schedule(
            schedule_id,
            {
                "last_status": "error",
                "last_error": str(exc),
                "last_finished_at": time.time(),
                "next_run_at": _next_run_at(schedule),
                "last_slug": slug,
            },
        )
        print(f"[scheduler] Failed '{schedule.get('name') or schedule_id}': {exc}", flush=True)
    finally:
        _running_schedule_ids.discard(schedule_id)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def _scheduler_loop() -> None:
    while True:
        now = time.time()
        for schedule in _load_schedules():
            if not schedule.get("enabled"):
                continue
            schedule_id = str(schedule.get("id"))
            if schedule_id in _running_schedule_ids:
                continue
            next_run_at = float(schedule.get("next_run_at") or _next_run_at(schedule, from_ts=now))
            if "next_run_at" not in schedule:
                _update_schedule(schedule_id, {"next_run_at": next_run_at})
                continue
            if next_run_at <= now:
                asyncio.create_task(_run_schedule(schedule))
        await asyncio.sleep(60)


@app.on_event("startup")
async def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        _scheduler_task = asyncio.create_task(_scheduler_loop())


# ─── API routes ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    try:
        env = _merged_env()
        result = subprocess.run(
            _ymf_cmd() + ["doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        try:
            details = json.loads(result.stdout)
        except Exception:
            details = {"output": result.stdout or result.stderr}
        return {"status": "ok" if result.returncode == 0 else "error", "details": details}
    except Exception as exc:
        return {"status": "error", "details": str(exc)}


@app.get("/api/categories")
def get_categories() -> dict:
    if not CATEGORIES_FILE.exists():
        raise HTTPException(404, "categories.yaml not found")
    return _load_categories()


@app.get("/api/settings")
def get_settings() -> dict:
    env = _load_env()
    return {key: env.get(key, os.getenv(key, "")) for key in SETTING_KEYS}


@app.get("/api/youtube/status")
def youtube_status(request: Request) -> dict:
    env = _merged_env()
    token_path = _youtube_token_path(env)
    redirect_uri = _youtube_redirect_uri(request, env)
    return {
        "connected": token_path.exists(),
        "token_file": str(token_path),
        "client_configured": _youtube_client_config(env, redirect_uri) is not None,
        "redirect_uri": redirect_uri,
    }


@app.post("/api/youtube/oauth/start")
def start_youtube_oauth(request: Request) -> dict:
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Google OAuth libraries are not installed") from exc

    env = _merged_env()
    redirect_uri = _youtube_redirect_uri(request, env)
    client_config = _youtube_client_config(env, redirect_uri)
    if client_config is None:
        raise HTTPException(400, "Configure YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET first")

    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(
        client_config,
        scopes=[YOUTUBE_UPLOAD_SCOPE],
        state=state,
        redirect_uri=redirect_uri,
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    state_path = _oauth_state_path(env)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "state": state,
                "redirect_uri": redirect_uri,
                "code_verifier": getattr(flow, "code_verifier", None),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {"authorization_url": authorization_url, "redirect_uri": redirect_uri}


@app.get("/api/youtube/oauth/callback", response_class=HTMLResponse)
def youtube_oauth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<h1>YouTube OAuth error</h1><p>{error}</p>", status_code=400)
    if not code or not state:
        return HTMLResponse("<h1>YouTube OAuth error</h1><p>Missing code or state.</p>", status_code=400)

    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Google OAuth libraries are not installed") from exc

    env = _merged_env()
    state_path = _oauth_state_path(env)
    if not state_path.exists():
        return HTMLResponse("<h1>YouTube OAuth error</h1><p>OAuth state expired. Start again.</p>", status_code=400)
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    if not secrets.compare_digest(saved.get("state", ""), state):
        return HTMLResponse("<h1>YouTube OAuth error</h1><p>Invalid OAuth state.</p>", status_code=400)

    redirect_uri = saved.get("redirect_uri") or _youtube_redirect_uri(request, env)
    client_config = _youtube_client_config(env, redirect_uri)
    if client_config is None:
        return HTMLResponse("<h1>YouTube OAuth error</h1><p>Missing OAuth client configuration.</p>", status_code=400)

    flow = Flow.from_client_config(
        client_config,
        scopes=[YOUTUBE_UPLOAD_SCOPE],
        state=state,
        redirect_uri=redirect_uri,
        code_verifier=saved.get("code_verifier"),
        autogenerate_code_verifier=False,
    )
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            "<h1>YouTube OAuth error</h1>"
            f"<p>{str(exc)}</p>"
            "<p>Check that the OAuth client has this redirect URI configured in Google Cloud.</p>",
            status_code=400,
        )

    token_path = _youtube_token_path(env)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
    try:
        token_path.chmod(0o600)
        state_path.unlink(missing_ok=True)
    except OSError:
        pass

    return HTMLResponse(
        """
        <!doctype html>
        <html lang="es">
          <head><meta charset="utf-8"><title>YouTube conectado</title></head>
          <body style="font-family:system-ui;background:#09090b;color:#fafafa;padding:32px">
            <h1>YouTube conectado</h1>
            <p>Ya puedes cerrar esta pestaña y volver al dashboard.</p>
            <script>try { window.opener && window.opener.postMessage({type:'youtube-oauth-connected'}, '*'); } catch(e) {}</script>
          </body>
        </html>
        """
    )


class SettingsBody(BaseModel):
    settings: dict[str, str]


@app.post("/api/settings")
def update_settings(body: SettingsBody) -> dict:
    filtered = {
        k: v
        for k, v in body.settings.items()
        if k in SETTING_KEYS and (v or not os.getenv(k))
    }
    _write_env(filtered)
    return {"status": "saved"}


class ScheduleBody(BaseModel):
    schedule: dict


@app.get("/api/schedules")
def list_schedules() -> list:
    return sorted(_load_schedules(), key=lambda item: item.get("created_at") or 0, reverse=True)


@app.post("/api/schedules")
def create_schedule(body: ScheduleBody) -> dict:
    schedule = dict(body.schedule)
    schedule["id"] = secrets.token_urlsafe(8)
    schedule["created_at"] = time.time()
    schedule["updated_at"] = schedule["created_at"]
    schedule["enabled"] = bool(schedule.get("enabled", True))
    schedule["frequency_value"] = float(schedule.get("frequency_value") or 24)
    schedule["frequency_unit"] = schedule.get("frequency_unit") if schedule.get("frequency_unit") in SCHEDULE_UNITS else "hours"
    if schedule.get("frequency_unit") == "days" and not schedule.get("run_time"):
        schedule["run_time"] = "09:00"
    schedule["next_run_at"] = _next_run_at(schedule)
    schedules = _load_schedules()
    schedules.append(schedule)
    _save_schedules(schedules)
    return schedule


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: ScheduleBody) -> dict:
    updates = dict(body.schedule)
    updates["updated_at"] = time.time()
    if "frequency_value" in updates:
        updates["frequency_value"] = float(updates["frequency_value"] or 24)
    if updates.get("frequency_unit") not in SCHEDULE_UNITS:
        updates.pop("frequency_unit", None)
    if any(key in updates for key in ("frequency_value", "frequency_unit", "run_time")):
        existing = next((item for item in _load_schedules() if item.get("id") == schedule_id), {})
        preview = {**existing, **updates}
        updates["next_run_at"] = _next_run_at(preview)
    updated = _update_schedule(schedule_id, updates)
    if updated is None:
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    return updated


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    schedules = _load_schedules()
    remaining = [schedule for schedule in schedules if schedule.get("id") != schedule_id]
    if len(remaining) == len(schedules):
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    _save_schedules(remaining)
    return {"status": "deleted"}


@app.post("/api/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str) -> dict:
    updated = _update_schedule(schedule_id, {"next_run_at": time.time(), "updated_at": time.time()})
    if updated is None:
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    if schedule_id not in _running_schedule_ids:
        asyncio.create_task(_run_schedule(updated))
    return updated


@app.get("/api/runs")
def list_runs() -> list:
    workdir = _get_workdir()
    if not workdir.exists():
        return []
    runs = []
    for run_dir in workdir.iterdir():
        if not run_dir.is_dir():
            continue
        snapshot = _run_snapshot(run_dir)
        if snapshot:
            runs.append(snapshot)
    return sorted(runs, key=lambda run: run.get("created_at") or 0, reverse=True)


@app.get("/api/runs/{slug}")
def get_run(slug: str) -> dict:
    workdir = _get_workdir()
    snapshot = _run_snapshot(workdir / slug)
    if snapshot is None:
        raise HTTPException(404, f"Run '{slug}' not found")
    return snapshot


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> str:
    return "User-agent: *\nDisallow:\n"


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(str(Path(__file__).parent / "static" / "favicon.svg"), media_type="image/svg+xml")


class JobBody(BaseModel):
    spec_yaml: str
    upload: bool = False


@app.post("/api/jobs")
async def run_job(body: JobBody) -> StreamingResponse:
    async def generate():
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(body.spec_yaml)
            tmp_path = f.name

        try:
            workdir = str(_get_workdir())
            flag = "--upload" if body.upload else "--no-upload"
            cmd = _ymf_cmd() + ["render", tmp_path, "--workdir", workdir, flag]

            env = _merged_env()
            print(f"[webui] Job request accepted: upload={body.upload}, workdir={workdir}", flush=True)
            print(f"[webui] Starting subprocess: {' '.join(cmd)}", flush=True)

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=env,
            )

            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                print(f"[job] {text}", flush=True)
                yield f"data: {json.dumps({'type': 'log', 'text': text})}\n\n"

            await process.wait()
            status = "success" if process.returncode == 0 else "error"
            print(f"[webui] Job subprocess finished: status={status}, code={process.returncode}", flush=True)
            yield f"data: {json.dumps({'type': 'done', 'status': status, 'code': process.returncode})}\n\n"

        except Exception as exc:
            print(f"[webui] Job failed before completion: {exc}", flush=True)
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download")
def download_file(path: str) -> FileResponse:
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = PROJECT_ROOT / file_path
    file_path = file_path.resolve()
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    workdir = _get_workdir()
    try:
        file_path.relative_to(workdir)
    except ValueError:
        raise HTTPException(403, "Access denied: path is outside workdir")
    return FileResponse(str(file_path), filename=file_path.name)


@app.get("/api/specs")
def list_specs() -> list:
    examples_dir = PROJECT_ROOT / "examples"
    specs = []
    for yaml_file in sorted(examples_dir.glob("*.yaml")):
        specs.append({
            "name": yaml_file.stem,
            "filename": yaml_file.name,
            "content": yaml_file.read_text(encoding="utf-8"),
        })
    return specs


# Static files (must be mounted last)
_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
