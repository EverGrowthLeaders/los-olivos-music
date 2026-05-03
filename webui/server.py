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
TENANTS_FILE = Path(
    os.getenv("YMF_TENANTS_FILE", "/app/.secrets/tenants.json" if Path("/app").exists() else str(PROJECT_ROOT / ".secrets" / "tenants.json"))
)
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SCHEDULE_UNITS = {"minutes": 60, "hours": 3600, "days": 86400}
DEFAULT_SCHEDULE_TIMEZONE = "Europe/Madrid"
DEFAULT_TENANT_ID = "default"
_scheduler_task: asyncio.Task | None = None
_running_schedule_ids: set[tuple[str, str]] = set()

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


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def _tenant_id(value: str | None) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]", "-", value or DEFAULT_TENANT_ID).strip("-").lower()
    return value or DEFAULT_TENANT_ID


def _tenant_root(tenant_id: str | None) -> Path:
    tenant_id = _tenant_id(tenant_id)
    return ENV_FILE.parent if tenant_id == DEFAULT_TENANT_ID else ENV_FILE.parent / "tenants" / tenant_id


def _tenant_env_file(tenant_id: str | None) -> Path:
    tenant_id = _tenant_id(tenant_id)
    return ENV_FILE if tenant_id == DEFAULT_TENANT_ID else _tenant_root(tenant_id) / ".env"


def _tenant_schedules_file(tenant_id: str | None) -> Path:
    tenant_id = _tenant_id(tenant_id)
    return SCHEDULES_FILE if tenant_id == DEFAULT_TENANT_ID else _tenant_root(tenant_id) / "schedules.json"


def _load_env(tenant_id: str | None = DEFAULT_TENANT_ID) -> dict[str, str]:
    tenant_id = _tenant_id(tenant_id)
    if tenant_id == DEFAULT_TENANT_ID:
        return _load_env_file(ENV_FILE)
    env = _load_env_file(ENV_FILE)
    env.update(_load_env_file(_tenant_env_file(tenant_id)))
    return env


def _tenant_env_overrides(tenant_id: str | None) -> dict[str, str]:
    return _load_env_file(_tenant_env_file(tenant_id))


def _merged_env(tenant_id: str | None = DEFAULT_TENANT_ID) -> dict[str, str]:
    tenant_id = _tenant_id(tenant_id)
    env = os.environ.copy()
    env.update(_load_env(tenant_id))
    if tenant_id != DEFAULT_TENANT_ID and "YOUTUBE_TOKEN_FILE" not in _tenant_env_overrides(tenant_id):
        env["YOUTUBE_TOKEN_FILE"] = str((_tenant_root(tenant_id) / "youtube-token.json").resolve())
    return env


def _write_env(updates: dict[str, str], tenant_id: str | None = DEFAULT_TENANT_ID) -> None:
    env_file = _tenant_env_file(tenant_id)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
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

    env_file.write_text("\n".join(result_lines) + "\n", encoding="utf-8")


def _base_workdir() -> Path:
    env = _load_env(DEFAULT_TENANT_ID)
    workdir = env.get("YMF_WORKDIR") or os.getenv("YMF_WORKDIR") or "./runs"
    path = Path(workdir)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _get_workdir(tenant_id: str | None = DEFAULT_TENANT_ID) -> Path:
    tenant_id = _tenant_id(tenant_id)
    env = _load_env(tenant_id)
    tenant_overrides = _tenant_env_overrides(tenant_id)
    if tenant_id != DEFAULT_TENANT_ID and "YMF_WORKDIR" not in tenant_overrides:
        return (_base_workdir() / "tenants" / tenant_id).resolve()
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


def _youtube_token_path(env: dict[str, str], tenant_id: str | None = DEFAULT_TENANT_ID) -> Path:
    tenant_id = _tenant_id(tenant_id)
    if tenant_id != DEFAULT_TENANT_ID and "YOUTUBE_TOKEN_FILE" not in _tenant_env_overrides(tenant_id):
        return (_tenant_root(tenant_id) / "youtube-token.json").resolve()
    return _path_from_env(env, "YOUTUBE_TOKEN_FILE", "/app/.secrets/youtube-token.json")


def _oauth_state_path(env: dict[str, str], tenant_id: str | None = DEFAULT_TENANT_ID) -> Path:
    return _youtube_token_path(env, tenant_id).with_name("youtube-oauth-state.json")


def _find_oauth_state(state: str) -> tuple[str, Path, dict] | None:
    for tenant in _load_tenants():
        tenant_id = str(tenant.get("id") or DEFAULT_TENANT_ID)
        env = _merged_env(tenant_id)
        state_path = _oauth_state_path(env, tenant_id)
        if not state_path.exists():
            continue
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if secrets.compare_digest(saved.get("state", ""), state):
            return tenant_id, state_path, saved
    return None


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


def _load_tenants() -> list[dict]:
    if TENANTS_FILE.exists():
        try:
            payload = json.loads(TENANTS_FILE.read_text(encoding="utf-8"))
            tenants = payload if isinstance(payload, list) else []
        except Exception:
            tenants = []
    else:
        tenants = []
    if not any(tenant.get("id") == DEFAULT_TENANT_ID for tenant in tenants):
        tenants.insert(
            0,
            {
                "id": DEFAULT_TENANT_ID,
                "name": "Canal principal",
                "created_at": 0,
            },
        )
    return tenants


def _save_tenants(tenants: list[dict]) -> None:
    TENANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TENANTS_FILE.write_text(json.dumps(tenants, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _tenant_exists(tenant_id: str | None) -> bool:
    tenant_id = _tenant_id(tenant_id)
    return any(tenant.get("id") == tenant_id for tenant in _load_tenants())


def _ensure_tenant(tenant_id: str | None) -> str:
    tenant_id = _tenant_id(tenant_id)
    if not _tenant_exists(tenant_id):
        raise HTTPException(404, f"Tenant '{tenant_id}' not found")
    return tenant_id


def _load_schedules(tenant_id: str | None = DEFAULT_TENANT_ID) -> list[dict]:
    schedules_file = _tenant_schedules_file(tenant_id)
    if not schedules_file.exists():
        return []
    try:
        payload = json.loads(schedules_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _save_schedules(schedules: list[dict], tenant_id: str | None = DEFAULT_TENANT_ID) -> None:
    schedules_file = _tenant_schedules_file(tenant_id)
    schedules_file.parent.mkdir(parents=True, exist_ok=True)
    schedules_file.write_text(json.dumps(schedules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def _schedule_spec_yaml(schedule: dict, tenant_id: str | None = DEFAULT_TENANT_ID) -> tuple[str, str]:
    categories = _load_categories()
    category_key = str(schedule.get("category_key") or "focus_lofi")
    category = categories.get(category_key, {})
    now = int(time.time())
    suffix = secrets.token_hex(3)
    title_seed = str(schedule.get("title_seed") or category.get("label") or "automatic music mix")
    slug = f"{_safe_slug(title_seed)}-{now}-{suffix}"
    env = _merged_env(tenant_id)
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


def _update_schedule(schedule_id: str, updates: dict, tenant_id: str | None = DEFAULT_TENANT_ID) -> dict | None:
    schedules = _load_schedules(tenant_id)
    updated = None
    for schedule in schedules:
        if schedule.get("id") == schedule_id:
            schedule.update(updates)
            updated = schedule
            break
    _save_schedules(schedules, tenant_id)
    return updated


async def _run_schedule(schedule: dict, tenant_id: str | None = DEFAULT_TENANT_ID) -> None:
    tenant_id = _tenant_id(tenant_id)
    schedule_id = str(schedule.get("id"))
    run_key = (tenant_id, schedule_id)
    if run_key in _running_schedule_ids:
        return
    _running_schedule_ids.add(run_key)
    spec_yaml, slug = _schedule_spec_yaml(schedule, tenant_id)
    _update_schedule(
        schedule_id,
        {
            "last_status": "running",
            "last_started_at": time.time(),
            "last_slug": slug,
        },
        tenant_id,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(spec_yaml)
        tmp_path = f.name

    try:
        upload = bool(schedule.get("upload", True))
        flag = "--upload" if upload else "--no-upload"
        cmd = _ymf_cmd() + ["render", tmp_path, "--workdir", str(_get_workdir(tenant_id)), flag]
        print(
            f"[scheduler] Starting tenant={tenant_id} '{schedule.get('name') or schedule_id}' "
            f"as {slug}: {' '.join(cmd)}",
            flush=True,
        )
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=_merged_env(tenant_id),
        )
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            print(f"[schedule:{tenant_id}:{schedule_id}] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
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
            tenant_id,
        )
        print(f"[scheduler] Finished tenant={tenant_id} '{schedule.get('name') or schedule_id}' status={status}", flush=True)
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
            tenant_id,
        )
        print(f"[scheduler] Failed tenant={tenant_id} '{schedule.get('name') or schedule_id}': {exc}", flush=True)
    finally:
        _running_schedule_ids.discard(run_key)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def _scheduler_loop() -> None:
    while True:
        now = time.time()
        for tenant in _load_tenants():
            tenant_id = str(tenant.get("id") or DEFAULT_TENANT_ID)
            for schedule in _load_schedules(tenant_id):
                if not schedule.get("enabled"):
                    continue
                schedule_id = str(schedule.get("id"))
                if (tenant_id, schedule_id) in _running_schedule_ids:
                    continue
                next_run_at = float(schedule.get("next_run_at") or _next_run_at(schedule, from_ts=now))
                if "next_run_at" not in schedule:
                    _update_schedule(schedule_id, {"next_run_at": next_run_at}, tenant_id)
                    continue
                if next_run_at <= now:
                    asyncio.create_task(_run_schedule(schedule, tenant_id))
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


class TenantBody(BaseModel):
    name: str


@app.get("/api/tenants")
def list_tenants() -> list:
    return _load_tenants()


@app.post("/api/tenants")
def create_tenant(body: TenantBody) -> dict:
    tenants = _load_tenants()
    tenant_id = _tenant_id(body.name)
    if tenant_id == DEFAULT_TENANT_ID or any(tenant.get("id") == tenant_id for tenant in tenants):
        tenant_id = f"{tenant_id}-{secrets.token_hex(2)}"
    tenant = {"id": tenant_id, "name": body.name.strip() or tenant_id, "created_at": time.time()}
    tenants.append(tenant)
    _save_tenants(tenants)
    _tenant_root(tenant_id).mkdir(parents=True, exist_ok=True)
    return tenant


@app.get("/api/settings")
def get_settings(tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    env = _merged_env(tenant_id)
    if tenant_id != DEFAULT_TENANT_ID and "YMF_WORKDIR" not in _tenant_env_overrides(tenant_id):
        env["YMF_WORKDIR"] = str(_get_workdir(tenant_id))
    return {key: env.get(key, os.getenv(key, "")) for key in SETTING_KEYS}


@app.get("/api/youtube/status")
def youtube_status(request: Request, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    env = _merged_env(tenant_id)
    token_path = _youtube_token_path(env, tenant_id)
    redirect_uri = _youtube_redirect_uri(request, env)
    return {
        "connected": token_path.exists(),
        "token_file": str(token_path),
        "client_configured": _youtube_client_config(env, redirect_uri) is not None,
        "redirect_uri": redirect_uri,
    }


@app.post("/api/youtube/oauth/start")
def start_youtube_oauth(request: Request, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Google OAuth libraries are not installed") from exc

    tenant_id = _ensure_tenant(tenant_id)
    env = _merged_env(tenant_id)
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
    state_path = _oauth_state_path(env, tenant_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "state": state,
                "tenant_id": tenant_id,
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

    found_state = _find_oauth_state(state)
    if found_state is None:
        return HTMLResponse("<h1>YouTube OAuth error</h1><p>Invalid OAuth state.</p>", status_code=400)
    tenant_id, state_path, saved = found_state
    env = _merged_env(tenant_id)

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

    token_path = _youtube_token_path(env, tenant_id)
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
def update_settings(body: SettingsBody, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    filtered = {
        k: v
        for k, v in body.settings.items()
        if k in SETTING_KEYS and (v or not os.getenv(k))
    }
    _write_env(filtered, tenant_id)
    return {"status": "saved"}


class ScheduleBody(BaseModel):
    schedule: dict


@app.get("/api/schedules")
def list_schedules(tenant_id: str = DEFAULT_TENANT_ID) -> list:
    tenant_id = _ensure_tenant(tenant_id)
    return sorted(_load_schedules(tenant_id), key=lambda item: item.get("created_at") or 0, reverse=True)


@app.post("/api/schedules")
def create_schedule(body: ScheduleBody, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
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
    schedules = _load_schedules(tenant_id)
    schedules.append(schedule)
    _save_schedules(schedules, tenant_id)
    return schedule


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, body: ScheduleBody, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    updates = dict(body.schedule)
    updates["updated_at"] = time.time()
    if "frequency_value" in updates:
        updates["frequency_value"] = float(updates["frequency_value"] or 24)
    if updates.get("frequency_unit") not in SCHEDULE_UNITS:
        updates.pop("frequency_unit", None)
    if any(key in updates for key in ("frequency_value", "frequency_unit", "run_time")):
        existing = next((item for item in _load_schedules(tenant_id) if item.get("id") == schedule_id), {})
        preview = {**existing, **updates}
        updates["next_run_at"] = _next_run_at(preview)
    updated = _update_schedule(schedule_id, updates, tenant_id)
    if updated is None:
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    return updated


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    schedules = _load_schedules(tenant_id)
    remaining = [schedule for schedule in schedules if schedule.get("id") != schedule_id]
    if len(remaining) == len(schedules):
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    _save_schedules(remaining, tenant_id)
    return {"status": "deleted"}


@app.post("/api/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    updated = _update_schedule(schedule_id, {"next_run_at": time.time(), "updated_at": time.time()}, tenant_id)
    if updated is None:
        raise HTTPException(404, f"Schedule '{schedule_id}' not found")
    if (tenant_id, schedule_id) not in _running_schedule_ids:
        asyncio.create_task(_run_schedule(updated, tenant_id))
    return updated


@app.get("/api/runs")
def list_runs(tenant_id: str = DEFAULT_TENANT_ID) -> list:
    tenant_id = _ensure_tenant(tenant_id)
    workdir = _get_workdir(tenant_id)
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
def get_run(slug: str, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = _ensure_tenant(tenant_id)
    workdir = _get_workdir(tenant_id)
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
async def run_job(body: JobBody, tenant_id: str = DEFAULT_TENANT_ID) -> StreamingResponse:
    tenant_id = _ensure_tenant(tenant_id)
    async def generate():
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(body.spec_yaml)
            tmp_path = f.name

        try:
            workdir = str(_get_workdir(tenant_id))
            flag = "--upload" if body.upload else "--no-upload"
            cmd = _ymf_cmd() + ["render", tmp_path, "--workdir", workdir, flag]

            env = _merged_env(tenant_id)
            print(f"[webui] Job request accepted: tenant={tenant_id}, upload={body.upload}, workdir={workdir}", flush=True)
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
def download_file(path: str, tenant_id: str = DEFAULT_TENANT_ID) -> FileResponse:
    tenant_id = _ensure_tenant(tenant_id)
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = PROJECT_ROOT / file_path
    file_path = file_path.resolve()
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    workdir = _get_workdir(tenant_id)
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
