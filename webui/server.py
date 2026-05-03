from __future__ import annotations

import asyncio
import secrets
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES_FILE = PROJECT_ROOT / "config" / "categories.yaml"
ENV_FILE = Path(os.getenv("YMF_ENV_FILE", "/app/.secrets/.env" if Path("/app").exists() else str(PROJECT_ROOT / ".env")))
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

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
    with open(CATEGORIES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)  # type: ignore[return-value]


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
    state_path = _oauth_state_path(env)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"state": state, "redirect_uri": redirect_uri}) + "\n", encoding="utf-8")

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


@app.get("/api/runs")
def list_runs() -> list:
    workdir = _get_workdir()
    if not workdir.exists():
        return []
    runs = []
    for result_file in sorted(
        workdir.glob("*/result.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
            runs.append({"slug": result_file.parent.name, "created_at": result_file.stat().st_mtime, **data})
        except Exception:
            pass
    return runs


@app.get("/api/runs/{slug}")
def get_run(slug: str) -> dict:
    workdir = _get_workdir()
    result_file = workdir / slug / "result.json"
    if not result_file.exists():
        raise HTTPException(404, f"Run '{slug}' not found")
    return json.loads(result_file.read_text(encoding="utf-8"))


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

            env = os.environ.copy()
            env.update(_load_env())

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
                yield f"data: {json.dumps({'type': 'log', 'text': text})}\n\n"

            await process.wait()
            status = "success" if process.returncode == 0 else "error"
            yield f"data: {json.dumps({'type': 'done', 'status': status, 'code': process.returncode})}\n\n"

        except Exception as exc:
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
