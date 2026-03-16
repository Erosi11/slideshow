import os
import json
import uuid
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)

MEDIA_DIR = Path("media")
CONFIG_FILE = Path("config.json")
MAX_IMAGE_DIM = (1920, 1080)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "pdf", "pptx", "ppt"}

MEDIA_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {"global_delay": 10, "slides": []}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Atomic write: write to temp file, then rename to prevent corruption."""
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


# ---------------------------------------------------------------------------
# File processing helpers
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _unique_name(suffix: str = ".jpg") -> str:
    return f"slide_{uuid.uuid4().hex[:10]}{suffix}"


def process_image(src_path: Path) -> str:
    """Convert any image to an optimized JPEG. Returns the saved filename."""
    name = _unique_name()
    with Image.open(src_path) as img:
        img = img.convert("RGB")
        img.thumbnail(MAX_IMAGE_DIM, Image.LANCZOS)
        img.save(MEDIA_DIR / name, "JPEG", quality=85, optimize=True)
    return name


def process_pdf(pdf_path: Path) -> list[str]:
    """Convert each PDF page to a JPEG. Returns list of saved filenames."""
    from pdf2image import convert_from_path

    filenames = []
    pages = convert_from_path(str(pdf_path), dpi=150, size=(1920, None))
    for page in pages:
        name = _unique_name()
        page = page.convert("RGB")
        page.thumbnail(MAX_IMAGE_DIM, Image.LANCZOS)
        page.save(MEDIA_DIR / name, "JPEG", quality=85, optimize=True)
        filenames.append(name)
        del page  # release RAM immediately (important on 512 MB)
    return filenames


def process_pptx(pptx_path: Path) -> list[str]:
    """Convert PPTX → PDF via LibreOffice headless, then PDF → JPEGs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmp_dir,
                str(pptx_path),
            ],
            capture_output=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed: {result.stderr.decode()}"
            )
        pdf_files = list(Path(tmp_dir).glob("*.pdf"))
        if not pdf_files:
            raise RuntimeError("LibreOffice produced no PDF output.")
        return process_pdf(pdf_files[0])


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.route("/")
def admin():
    return render_template("index.html")


@app.route("/display")
def display():
    return render_template("display.html")


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def api_update_config():
    data = request.get_json(force=True)
    config = load_config()

    if "global_delay" in data:
        config["global_delay"] = max(1, int(data["global_delay"]))

    if "slides" in data:
        # Validate: only accept slides that reference existing media files
        existing = {f.name for f in MEDIA_DIR.iterdir()}
        config["slides"] = [
            s for s in data["slides"] if s.get("filename") in existing
        ]

    save_config(config)
    return jsonify({"status": "ok"})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "Unsupported file type"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    suffix = f".{ext}"

    # Save upload to a temporary path
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    tmp_path = Path(tmp_path)
    try:
        os.close(fd)
        file.save(tmp_path)

        config = load_config()
        new_filenames: list[str] = []

        if ext == "pdf":
            new_filenames = process_pdf(tmp_path)
        elif ext in ("pptx", "ppt"):
            new_filenames = process_pptx(tmp_path)
        else:
            new_filenames = [process_image(tmp_path)]

        # Append new slides after existing ones
        next_order = max((s["order"] for s in config["slides"]), default=0) + 1
        for i, fname in enumerate(new_filenames):
            config["slides"].append(
                {"filename": fname, "delay_override": None, "order": next_order + i}
            )

        save_config(config)
        return jsonify({"status": "ok", "files": new_filenames})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    finally:
        tmp_path.unlink(missing_ok=True)


@app.route("/api/slide/<filename>", methods=["DELETE"])
def api_delete_slide(filename):
    # Sanitise: only allow bare filenames, no path traversal
    safe = Path(secure_filename(filename))
    if safe.name != filename:
        return jsonify({"error": "Invalid filename"}), 400

    config = load_config()
    config["slides"] = [s for s in config["slides"] if s["filename"] != filename]

    # Re-number orders sequentially
    for i, slide in enumerate(sorted(config["slides"], key=lambda x: x["order"]), 1):
        slide["order"] = i

    save_config(config)

    media_file = MEDIA_DIR / safe
    if media_file.exists():
        media_file.unlink()

    return jsonify({"status": "ok"})


@app.route("/media/<path:filename>")
def serve_media(filename):
    return send_from_directory(MEDIA_DIR, filename)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Initialise config if missing
    if not CONFIG_FILE.exists():
        save_config({"global_delay": 10, "slides": []})
    app.run(host="0.0.0.0", port=5000, debug=False)
