from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES_FILE = PROJECT_ROOT / "config" / "categories.yaml"
ENV_FILE = PROJECT_ROOT / ".env"

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


def _write_env(updates: dict[str, str]) -> None:
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
    return (PROJECT_ROOT / workdir).resolve()


# ─── API routes ───────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    try:
        result = subprocess.run(
            _ymf_cmd() + ["doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
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
    return {key: env.get(key, "") for key in SETTING_KEYS}


class SettingsBody(BaseModel):
    settings: dict[str, str]


@app.post("/api/settings")
def update_settings(body: SettingsBody) -> dict:
    filtered = {k: v for k, v in body.settings.items() if k in SETTING_KEYS}
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
