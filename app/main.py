import os
import uuid
import json
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="FFmpeg Metadata Tool")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logs: list[dict] = []
log_lock = threading.Lock()


def add_log(message: str, level: str = "info"):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
    }
    with log_lock:
        logs.append(entry)
        if len(logs) > 500:
            logs.pop(0)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    allowed = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        add_log(f"Rejected: {file.filename} (unsupported format)", "warn")
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    file_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{file_id}{ext}"
    output_path = OUTPUT_DIR / f"{file_id}_clean{ext}"

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = input_path.stat().st_size
    add_log(f"Uploaded: {file.filename} ({format_size(file_size)}) -> {file_id}")

    try:
        add_log(f"Stripping metadata: {file.filename}")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-map_metadata", "-1",
                "-c", "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"FFmpeg error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")

        output_size = output_path.stat().st_size
        add_log(f"Done: {file.filename} ({format_size(output_size)})")
    except subprocess.TimeoutExpired:
        add_log(f"Timeout: {file.filename}", "error")
        raise HTTPException(status_code=500, detail="Processing timed out")

    return {
        "file_id": file_id,
        "original_name": file.filename,
        "download_name": f"{Path(file.filename).stem}_clean{ext}",
        "original_size": file_size,
        "clean_size": output_path.stat().st_size,
    }


@app.get("/api/metadata/{file_id}")
async def get_metadata(file_id: str):
    files = list(OUTPUT_DIR.glob(f"{file_id}_clean.*"))
    if not files:
        files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not files:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = files[0]
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="ffprobe failed")

        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        duration = data.get("format", {}).get("duration", "0")
        size = data.get("format", {}).get("size", "0")

        add_log(f"Metadata read for {file_id}")
        return {
            "file_id": file_id,
            "tags": tags,
            "duration": float(duration),
            "size": int(size),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="ffprobe timeout")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse ffprobe output")


class MetadataUpdate(BaseModel):
    tags: dict[str, str]


@app.post("/api/metadata/{file_id}")
async def update_metadata(file_id: str, body: MetadataUpdate):
    source_files = list(OUTPUT_DIR.glob(f"{file_id}_clean.*"))
    if not source_files:
        source_files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not source_files:
        raise HTTPException(status_code=404, detail="File not found")

    source_path = source_files[0]
    ext = source_path.suffix
    output_path = OUTPUT_DIR / f"{file_id}_clean{ext}"

    ffmpeg_args = ["ffmpeg", "-y", "-i", str(source_path)]
    for key, value in body.tags.items():
        if value.strip() == "":
            ffmpeg_args.extend(["-metadata:s:v", f"{key}="])
            ffmpeg_args.extend(["-metadata:s:a", f"{key}="])
            ffmpeg_args.extend(["-metadata", f"{key}="])
        else:
            ffmpeg_args.extend(["-metadata", f"{key}={value}"])
    ffmpeg_args.extend(["-c", "copy", str(output_path)])

    try:
        add_log(f"Updating metadata for {file_id}: {list(body.tags.keys())}")
        result = subprocess.run(
            ffmpeg_args,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"Metadata update error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")

        add_log(f"Metadata updated for {file_id}")
        return {"status": "ok", "file_id": file_id}
    except subprocess.TimeoutExpired:
        add_log(f"Metadata update timeout: {file_id}", "error")
        raise HTTPException(status_code=500, detail="Processing timed out")


@app.get("/api/stream/{file_id}")
async def stream_video(file_id: str):
    files = list(OUTPUT_DIR.glob(f"{file_id}_clean.*"))
    if not files:
        files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not files:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = files[0]
    ext = file_path.suffix.lower()

    mime_map = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".flv": "video/x-flv",
        ".wmv": "video/x-ms-wmv",
        ".m4v": "video/mp4",
        ".ts": "video/mp2t",
        ".mpg": "video/mpeg",
        ".mpeg": "video/mpeg",
    }
    mime = mime_map.get(ext, "video/mp4")

    file_size = file_path.stat().st_size

    range_header = None
    async def file_generator():
        with open(file_path, "rb") as f:
            yield f.read()

    return FileResponse(
        path=str(file_path),
        media_type=mime,
        filename=None,
    )


@app.get("/api/download/{file_id}")
async def download_video(file_id: str):
    files = list(OUTPUT_DIR.glob(f"{file_id}_clean.*"))
    if not files:
        raise HTTPException(status_code=404, detail="File not found")

    output_path = files[0]
    ext = output_path.suffix
    original_files = list(UPLOAD_DIR.glob(f"{file_id}{ext}"))
    original_name = Path(original_files[0]).stem if original_files else file_id
    download_name = f"{original_name}_clean{ext}"

    add_log(f"Download: {download_name}")
    return FileResponse(
        path=str(output_path),
        filename=download_name,
        media_type="application/octet-stream",
    )


@app.get("/api/logs")
async def get_logs(limit: int = 200):
    with log_lock:
        return {"logs": logs[-limit:]}


@app.on_event("startup")
async def startup():
    add_log("Server started")


def format_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1048576:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1048576:.2f} MB"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
