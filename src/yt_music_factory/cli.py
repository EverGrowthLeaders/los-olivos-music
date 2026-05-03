from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

from .config import category_for, load_categories, load_spec
from .pipeline import run_pipeline
from .seo import build_metadata
from .utils import require_binary, write_json
from .youtube import upload_video


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="ymf", description="AI music video factory for YouTube")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="Generate music/images, render video, and optionally upload")
    render.add_argument("spec", type=Path)
    render.add_argument("--workdir", type=Path, default=Path(os.getenv("YMF_WORKDIR", "runs")))
    upload_group = render.add_mutually_exclusive_group()
    upload_group.add_argument("--upload", action="store_true", help="Upload after rendering")
    upload_group.add_argument("--no-upload", action="store_true", help="Render only")

    seo = sub.add_parser("seo", help="Generate YouTube metadata JSON only")
    seo.add_argument("spec", type=Path)
    seo.add_argument("--out", type=Path, default=None)

    upload = sub.add_parser("upload", help="Upload an existing MP4 with metadata JSON")
    upload.add_argument("video", type=Path)
    upload.add_argument("metadata", type=Path)
    upload.add_argument("--thumbnail", type=Path, default=None)
    upload.add_argument("--client-secrets", type=Path, default=None)
    upload.add_argument("--token-file", type=Path, default=None)

    doctor = sub.add_parser("doctor", help="Check local binaries and provider environment")
    doctor.add_argument("--json", action="store_true")

    serve = sub.add_parser("serve", help="Launch the web UI (requires webui extras)")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

    args = parser.parse_args(argv)
    if args.command == "render":
        upload_value = True if args.upload else False if args.no_upload else None
        result = run_pipeline(args.spec, workdir=args.workdir, upload=upload_value)
        print(json.dumps(result.to_json(), indent=2, ensure_ascii=False))
    elif args.command == "seo":
        spec = load_spec(args.spec)
        categories = load_categories()
        metadata = build_metadata(spec, category_for(spec, categories))
        payload = metadata.to_json()
        if args.out:
            write_json(args.out, payload)
            print(str(args.out))
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.command == "upload":
        from .seo import VideoMetadata

        raw = json.loads(args.metadata.read_text(encoding="utf-8"))
        snippet = raw.get("snippet", {})
        status = raw.get("status", {})
        metadata = VideoMetadata(
            title=snippet["title"],
            description=snippet.get("description", ""),
            tags=snippet.get("tags", []),
            category_id=snippet.get("categoryId", "10"),
            language=snippet.get("defaultLanguage", "en"),
            contains_synthetic_media=bool(status.get("containsSyntheticMedia", True)),
            made_for_kids=bool(status.get("selfDeclaredMadeForKids", False)),
            privacy_status=status.get("privacyStatus", "private"),
            notify_subscribers=bool(raw.get("notifySubscribers", False)),
            publish_at=status.get("publishAt"),
        )
        video_id = upload_video(
            args.video,
            metadata,
            client_secrets=args.client_secrets,
            token_file=args.token_file,
            thumbnail_path=args.thumbnail,
        )
        print(json.dumps({"video_id": video_id}, indent=2))
    elif args.command == "doctor":
        report = doctor_report()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            for key, value in report.items():
                print(f"{key}: {value}")
    elif args.command == "serve":
        import sys as _sys
        from pathlib import Path as _Path

        try:
            import uvicorn  # noqa: F401
        except ImportError:
            print("Error: webui dependencies not installed.")
            print("Run: pip install -e '.[webui]'  or  pip install fastapi uvicorn")
            _sys.exit(1)

        # Ensure project root is on sys.path so `webui.server` is importable
        _project_root = str(_Path(__file__).resolve().parent.parent.parent)
        if _project_root not in _sys.path:
            _sys.path.insert(0, _project_root)

        import uvicorn

        print(f"Starting YT Music Factory UI on http://{args.host}:{args.port}")
        uvicorn.run(
            "webui.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


def doctor_report() -> dict[str, str | bool]:
    report: dict[str, str | bool] = {}
    for binary in ["ffmpeg", "ffprobe"]:
        try:
            report[binary] = require_binary(binary)
        except Exception as exc:  # noqa: BLE001
            report[binary] = f"missing: {exc}"
    report["ELEVENLABS_API_KEY"] = bool(os.getenv("ELEVENLABS_API_KEY"))
    report["GEMINI_API_KEY"] = bool(os.getenv("GEMINI_API_KEY"))
    report["MUBERT_CUSTOMER_ID"] = bool(os.getenv("MUBERT_CUSTOMER_ID"))
    report["MUBERT_ACCESS_TOKEN"] = bool(os.getenv("MUBERT_ACCESS_TOKEN"))
    client_secrets = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secret.json"))
    report["YOUTUBE_CLIENT_SECRETS"] = str(client_secrets) if client_secrets.exists() else False
    report["YOUTUBE_CLIENT_ID"] = bool(os.getenv("YOUTUBE_CLIENT_ID"))
    report["YOUTUBE_CLIENT_SECRET"] = bool(os.getenv("YOUTUBE_CLIENT_SECRET"))
    report["yt_music_factory_cli"] = bool(shutil.which("ymf")) or "available via python -m yt_music_factory.cli"
    return report


if __name__ == "__main__":
    main()
