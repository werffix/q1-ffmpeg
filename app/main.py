import os
import uuid
import json
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="FFmpeg Metadata Tool")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DOWNLOAD_DIR = BASE_DIR / "downloads"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
DOWNLOAD_DIR.mkdir(exist_ok=True)

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
            ["ffmpeg", "-y", "-i", str(input_path), "-map_metadata", "-1", "-c", "copy", str(output_path)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"FFmpeg error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")

        add_log(f"Done: {file.filename} ({format_size(output_path.stat().st_size)})")
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

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(files[0])],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="ffprobe failed")

        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        duration = float(data.get("format", {}).get("duration", "0"))
        size = int(data.get("format", {}).get("size", "0"))

        add_log(f"Metadata read for {file_id}: {len(tags)} tags")
        return {"file_id": file_id, "tags": tags, "duration": duration, "size": size}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="ffprobe timeout")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse ffprobe output")


class MetadataUpdate(BaseModel):
    tags: dict[str, str]


@app.post("/api/metadata/{file_id}")
async def update_metadata(file_id: str, body: MetadataUpdate):
    source_files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not source_files:
        raise HTTPException(status_code=404, detail="Original file not found")

    source_path = source_files[0]
    ext = source_path.suffix
    output_path = OUTPUT_DIR / f"{file_id}_clean{ext}"

    cmd = ["ffmpeg", "-y", "-i", str(source_path), "-map_metadata", "-1"]
    for key, value in body.tags.items():
        if key and value is not None:
            cmd.extend(["-metadata", f"{key}={value}"])
    cmd.extend(["-c", "copy", str(output_path)])

    try:
        add_log(f"Updating metadata for {file_id}: {list(body.tags.keys())}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"Metadata update error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")

        add_log(f"Metadata updated for {file_id}")
        return {"status": "ok", "file_id": file_id}
    except subprocess.TimeoutExpired:
        add_log(f"Metadata update timeout: {file_id}", "error")
        raise HTTPException(status_code=500, detail="Processing timed out")


@app.get("/api/strip-download/{file_id}")
async def strip_and_download(file_id: str):
    source_files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not source_files:
        raise HTTPException(status_code=404, detail="File not found")

    source_path = source_files[0]
    ext = source_path.suffix
    output_path = OUTPUT_DIR / f"{file_id}_stripped{ext}"

    try:
        add_log(f"Strip+download for {file_id}")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(source_path), "-map_metadata", "-1", "-c", "copy", str(output_path)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"Strip error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")

        original_name = source_path.stem
        download_name = f"{original_name}_stripped{ext}"
        add_log(f"Strip done: {download_name}")
        return FileResponse(
            path=str(output_path),
            filename=download_name,
            media_type="application/octet-stream",
        )
    except subprocess.TimeoutExpired:
        add_log(f"Strip timeout: {file_id}", "error")
        raise HTTPException(status_code=500, detail="Processing timed out")


@app.get("/api/stream/{file_id}")
async def stream_video(file_id: str):
    files = list(OUTPUT_DIR.glob(f"{file_id}_clean.*"))
    if not files:
        files = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not files:
        raise HTTPException(status_code=404, detail="File not found")

    ext = files[0].suffix.lower()
    mime_map = {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".flv": "video/x-flv",
        ".wmv": "video/x-ms-wmv", ".m4v": "video/mp4", ".ts": "video/mp2t",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    }
    return FileResponse(path=str(files[0]), media_type=mime_map.get(ext, "video/mp4"))


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
    return FileResponse(path=str(output_path), filename=download_name, media_type="application/octet-stream")


# --- Downloader ---

class UrlRequest(BaseModel):
    url: str


def _find_downloaded_file(directory: Path, before_files: set) -> Path | None:
    import time
    time.sleep(1)
    for _ in range(10):
        current = set(directory.iterdir())
        new_files = current - before_files
        video_exts = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.m4v', '.ts', '.mpg'}
        for f in sorted(new_files, key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix.lower() in video_exts and f.stat().st_size > 0:
                return f
        time.sleep(0.5)
    return None


@app.post("/api/dl/download")
async def dl_download(body: UrlRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Empty URL")

    dl_id = str(uuid.uuid4())[:8]
    before_files = set(DOWNLOAD_DIR.iterdir())

    add_log(f"Download requested: {url}")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-warnings",
                "-o", str(DOWNLOAD_DIR / f"{dl_id}_%(title)s.%(ext)s"),
                "--no-playlist",
                url,
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"yt-dlp error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"Download error: {err}")

        video_path = _find_downloaded_file(DOWNLOAD_DIR, before_files)
        if not video_path:
            add_log("Downloaded file not found", "error")
            raise HTTPException(status_code=500, detail="Downloaded file not found")

        file_id = video_path.stem.split('_')[0]
        add_log(f"Downloaded: {video_path.name} ({format_size(video_path.stat().st_size)})")

        return {
            "file_id": dl_id,
            "filename": video_path.name,
            "size": video_path.stat().st_size,
        }
    except subprocess.TimeoutExpired:
        add_log(f"Download timeout: {url}", "error")
        raise HTTPException(status_code=500, detail="Download timed out")


@app.post("/api/dl/download-stripped")
async def dl_download_stripped(body: UrlRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Empty URL")

    dl_id = str(uuid.uuid4())[:8]
    before_files = set(DOWNLOAD_DIR.iterdir())

    add_log(f"Download stripped requested: {url}")

    tmp_path = DOWNLOAD_DIR / f"{dl_id}_tmp.mp4"
    out_path = DOWNLOAD_DIR / f"{dl_id}_stripped.mp4"

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-warnings",
                "-o", str(tmp_path),
                "--no-playlist",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                url,
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            err = result.stderr[:500]
            add_log(f"yt-dlp error: {err}", "error")
            raise HTTPException(status_code=500, detail=f"Download error: {err}")

        if not tmp_path.exists():
            downloaded = _find_downloaded_file(DOWNLOAD_DIR, before_files)
            if downloaded:
                tmp_path = downloaded
            else:
                add_log("Downloaded file not found", "error")
                raise HTTPException(status_code=500, detail="Downloaded file not found")

        add_log(f"Stripping metadata from downloaded file")
        strip_result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_path), "-map_metadata", "-1", "-c", "copy", str(out_path)],
            capture_output=True, text=True, timeout=300,
        )

        if strip_result.returncode != 0:
            add_log(f"Strip failed, returning original", "warn")
            return FileResponse(
                path=str(tmp_path),
                filename=tmp_path.name,
                media_type="application/octet-stream",
            )

        add_log(f"Stripped: {out_path.name} ({format_size(out_path.stat().st_size)})")
        return FileResponse(
            path=str(out_path),
            filename=out_path.name,
            media_type="application/octet-stream",
        )
    except subprocess.TimeoutExpired:
        add_log(f"Download+strip timeout: {url}", "error")
        raise HTTPException(status_code=500, detail="Processing timed out")


@app.get("/api/dl/file/{filename}")
async def dl_serve_file(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(file_path), media_type="application/octet-stream")


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
