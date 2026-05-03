from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

import requests


class CommandError(RuntimeError):
    """Raised when a subprocess exits with a non-zero status."""


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "job"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required binary not found on PATH: {name}")
    return path


def run_cmd(args: Sequence[str], *, cwd: Path | None = None, quiet: bool = False) -> None:
    if not quiet:
        print("$", " ".join(str(a) for a in args))
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        msg = [f"Command failed with exit code {proc.returncode}: {' '.join(map(str, args))}"]
        if proc.stdout:
            msg.append("STDOUT:\n" + proc.stdout[-4000:])
        if proc.stderr:
            msg.append("STDERR:\n" + proc.stderr[-4000:])
        raise CommandError("\n".join(msg))
    if not quiet and proc.stdout.strip():
        print(proc.stdout.strip())


def run_cmd_json(args: Sequence[str]) -> dict:
    proc = subprocess.run(
        [str(a) for a in args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise CommandError(proc.stderr or proc.stdout or f"Command failed: {args}")
    return json.loads(proc.stdout)


def download_file(url: str, destination: Path, *, timeout: int = 300) -> Path:
    ensure_dir(destination.parent)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return destination


def write_json(path: Path, payload: dict) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def unique_ordered(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result
