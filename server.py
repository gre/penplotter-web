"""
penplotter/web — FastAPI backend
Wraps pyaxidraw / axicli to provide:
  - SVG upload, file management
  - Plot start / pause / resume / stop
  - Real-time status via SSE
  - Configuration, estimation
"""

import asyncio
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("AXICLI_CONFIG", str(APP_DIR / "db" / "axidraw.conf.py")))
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
AXICLI_CMD = "axicli"

_DUMMY_SVG = Path(tempfile.gettempdir()) / "axidraw_dummy.svg"
if not _DUMMY_SVG.exists():
    _DUMMY_SVG.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>')

app = FastAPI(title="penplotter/web")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

MAX_LOG_LINES = 200


# ---------------------------------------------------------------------------
# Plotter state
# ---------------------------------------------------------------------------

class PlotterState(str, Enum):
    IDLE = "idle"
    PLOTTING = "plotting"
    PAUSED = "paused"
    ERROR = "error"


class PlotterManager:
    """Manages a single AxiDraw plot subprocess."""

    def __init__(self):
        self.state: PlotterState = PlotterState.IDLE
        self.current_file: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None
        self.progress_pct: float = 0.0
        self.error_msg: Optional[str] = None
        self.log_lines: list[str] = []
        self._lock = threading.RLock()
        self._paused_file: Optional[str] = None
        self._plot_start_time: Optional[float] = None
        self.selected_file: Optional[str] = None
        self._log_version: int = 0

    def status_dict(self) -> dict:
        with self._lock:
            elapsed = None
            if self._plot_start_time and self.state in (PlotterState.PLOTTING, PlotterState.PAUSED):
                elapsed = int(time.time() - self._plot_start_time)
            return {
                "state": self.state.value,
                "current_file": self.current_file,
                "progress": self.progress_pct,
                "elapsed": elapsed,
                "error": self.error_msg,
                "log": self.log_lines[-50:],
                "log_version": self._log_version,
                "selected_file": self.selected_file,
                "can_home": self._paused_file is not None,
            }

    def start_plot(self, svg_path: str, options: dict | None = None):
        with self._lock:
            if self.state == PlotterState.PLOTTING:
                raise RuntimeError("A plot is already running")
            self.state = PlotterState.PLOTTING
            self.current_file = os.path.basename(svg_path)
            self.progress_pct = 0.0
            self.error_msg = None
            self.log_lines = []
            self._paused_file = None
            self._plot_start_time = time.time()

        thread = threading.Thread(target=self._run_plot, args=(svg_path, options), daemon=True)
        thread.start()

    def _build_cmd(self, svg_path: str, options: dict | None = None, resume: bool = False) -> list[str]:
        cmd = [AXICLI_CMD, svg_path]
        if CONFIG_PATH.exists():
            cmd += ["--config", str(CONFIG_PATH)]
        if resume and self._paused_file:
            cmd += ["--mode", "res_plot"]
        cmd += ["--output_file", svg_path, "--progress", "--report_time"]
        if options:
            for k, v in options.items():
                cmd += [f"--{k}", str(v)]
        return cmd

    def _parse_progress(self, text: str):
        """Parse tqdm progress output like '45% 3542/7845 mm'."""
        m = re.search(r'(\d+)%\s+\d+/\d+', text)
        if not m:
            m = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
        if m:
            with self._lock:
                self.progress_pct = min(float(m.group(1)), 100.0)

    def _run_plot(self, svg_path: str, options: dict | None = None, resume: bool = False):
        try:
            cmd = self._build_cmd(svg_path, options, resume)
            self._append_log(f"$ {' '.join(cmd)}")

            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=str(UPLOAD_DIR), start_new_session=True,
            )

            sel = selectors.DefaultSelector()
            sel.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            sel.register(self.process.stderr, selectors.EVENT_READ, "stderr")

            stderr_buf = ""
            open_fds = 2

            while open_fds > 0:
                events = sel.select(timeout=1.0)
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        sel.unregister(key.fileobj)
                        open_fds -= 1
                        continue
                    text = chunk.decode("utf-8", errors="replace")
                    if key.data == "stderr":
                        stderr_buf += text
                        parts = stderr_buf.split('\r')
                        stderr_buf = parts[-1]
                        for part in parts[:-1]:
                            part = part.strip()
                            if part:
                                self._parse_progress(part)
                        if stderr_buf.strip():
                            self._parse_progress(stderr_buf.strip())
                    else:
                        for line in text.splitlines():
                            line = line.strip()
                            if line:
                                self._append_log(line)
                                self._parse_progress(line)

                if self.process.poll() is not None and open_fds > 0:
                    time.sleep(0.2)
                    for key in list(sel.get_map().values()):
                        try:
                            sel.unregister(key.fileobj)
                        except Exception:
                            pass
                    open_fds = 0

            if stderr_buf.strip():
                for line in stderr_buf.strip().splitlines():
                    line = line.strip()
                    if line:
                        self._append_log(line)

            sel.close()
            self.process.wait(timeout=10)

            with self._lock:
                if self.state == PlotterState.IDLE:
                    pass  # stopped by user
                elif self.state == PlotterState.PAUSED:
                    pass  # paused by user
                elif self.process.returncode == 0:
                    self.state = PlotterState.IDLE
                    self.progress_pct = 100.0
                else:
                    self.state = PlotterState.ERROR
                    self.error_msg = f"axicli exited with code {self.process.returncode}"
        except Exception as e:
            with self._lock:
                self.state = PlotterState.ERROR
                self.error_msg = str(e)
                self._append_log(f"ERROR: {e}")
        finally:
            proc = self.process
            self.process = None
            if proc:
                try:
                    proc.stdout.close()
                    proc.stderr.close()
                except Exception:
                    pass
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass

    def pause(self):
        with self._lock:
            if self.state != PlotterState.PLOTTING or self.process is None:
                raise RuntimeError("Nothing to pause")
            os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
            self.state = PlotterState.PAUSED
            self._paused_file = self.current_file
            self._append_log("Pause requested (SIGINT sent)")

    def resume(self):
        with self._lock:
            if self.state != PlotterState.PAUSED:
                raise RuntimeError("Not paused")
            if not self._paused_file:
                raise RuntimeError("No state file to resume from")
            svg_path = str(UPLOAD_DIR / self._paused_file)
            self.state = PlotterState.PLOTTING
            self._append_log("Resuming plot...")

        thread = threading.Thread(target=self._run_plot, args=(svg_path, None, True), daemon=True)
        thread.start()

    def stop(self):
        with self._lock:
            proc = self.process
            if proc:
                self._paused_file = self.current_file
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                self._append_log("Plot stopped")
            self.state = PlotterState.IDLE
            self.current_file = None
            self.progress_pct = 0.0

    # -- Manual commands (unified) --

    def _run_cmd(self, cmd: list[str], timeout: int = 30):
        """Run an axicli command synchronously, log output and errors."""
        self._append_log(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout:
            self._append_log(result.stdout.strip())
        if result.returncode != 0 and result.stderr:
            self._append_log(f"ERROR: {result.stderr.strip()}")
        return result

    def _manual_cmd(self, cmd_name: str):
        with self._lock:
            if self.state == PlotterState.PLOTTING:
                raise RuntimeError("Cannot run manual command while plotting")
        cmd = [AXICLI_CMD, str(_DUMMY_SVG), "--mode", "manual", "--manual_cmd", cmd_name]
        if CONFIG_PATH.exists():
            cmd += ["--config", str(CONFIG_PATH)]
        try:
            self._run_cmd(cmd)
        except Exception as e:
            self._append_log(f"ERROR: {e}")

    def pen_up(self):
        self._manual_cmd("raise_pen")

    def pen_down(self):
        self._manual_cmd("lower_pen")

    def walk(self, dx_mm: float, dy_mm: float):
        with self._lock:
            if self.state == PlotterState.PLOTTING:
                raise RuntimeError("Cannot move while plotting")
        try:
            for axis, dist in [("walk_mmx", dx_mm), ("walk_mmy", dy_mm)]:
                if dist == 0:
                    continue
                cmd = [AXICLI_CMD, str(_DUMMY_SVG), "--mode", "manual",
                       "--manual_cmd", axis, "--dist", str(dist)]
                if CONFIG_PATH.exists():
                    cmd += ["--config", str(CONFIG_PATH)]
                self._run_cmd(cmd)
        except Exception as e:
            self._append_log(f"ERROR: {e}")

    def go_home(self):
        with self._lock:
            if self.state == PlotterState.PLOTTING:
                raise RuntimeError("Cannot go home while plotting")
            if not self._paused_file:
                raise RuntimeError("No paused plot to return home from")
            svg_path = str(UPLOAD_DIR / self._paused_file)
        cmd = [AXICLI_CMD, svg_path, "--mode", "res_home"]
        if CONFIG_PATH.exists():
            cmd += ["--config", str(CONFIG_PATH)]
        try:
            result = self._run_cmd(cmd, timeout=60)
            if result.returncode == 0:
                with self._lock:
                    self._paused_file = None
                    self.state = PlotterState.IDLE
                    self.current_file = None
                    self.progress_pct = 0.0
        except Exception as e:
            self._append_log(f"ERROR: {e}")

    def _append_log(self, line: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_lines.append(f"[{ts}] {line}")
            if len(self.log_lines) > MAX_LOG_LINES:
                self.log_lines = self.log_lines[-MAX_LOG_LINES:]
            self._log_version += 1


plotter = PlotterManager()


# ---------------------------------------------------------------------------
# Camera streaming (optional)
# ---------------------------------------------------------------------------

CAMERA_CONFIG_PATH = APP_DIR / "db" / "camera.json"


def _load_camera_config() -> dict:
    if CAMERA_CONFIG_PATH.exists():
        try:
            return json.loads(CAMERA_CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_camera_config(cfg: dict):
    CAMERA_CONFIG_PATH.parent.mkdir(exist_ok=True)
    CAMERA_CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


class CameraManager:
    """Manages a camera subprocess that outputs MJPEG to stdout.

    The command is user-configured via db/camera.json. No command = no camera.
    A background reader thread parses JPEG frames and stores the latest.
    The camera auto-stops when no client has polled for IDLE_TIMEOUT seconds.
    """

    IDLE_TIMEOUT = 5  # seconds without clients before stopping

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._clients: int = 0
        self._latest_frame: Optional[bytes] = None
        self._frame_event = asyncio.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_timer: Optional[threading.Timer] = None
        self._command: Optional[str] = None
        self._load_command()

    def _load_command(self):
        cfg = _load_camera_config()
        self._command = cfg.get("command") or None

    def is_available(self) -> bool:
        return self._command is not None

    def get_latest_frame(self) -> Optional[bytes]:
        return self._latest_frame

    def _start(self):
        """Start the configured camera command."""
        cmd = self._command
        if not cmd:
            return
        with self._lock:
            if self._process and self._process.poll() is None:
                return
            self._process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()

    def _stop(self):
        with self._lock:
            proc = self._process
            self._process = None
            self._latest_frame = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def _read_loop(self):
        """Background thread: read MJPEG stream, extract frames."""
        proc = self._process
        if not proc or not proc.stdout:
            return
        buf = b""
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        while proc.poll() is None:
            try:
                chunk = proc.stdout.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(b"\xff\xd8")
                if start == -1:
                    buf = b""
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end == -1:
                    buf = buf[start:]
                    break
                frame = buf[start:end + 2]
                buf = buf[end + 2:]
                self._latest_frame = frame
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(self._frame_event.set)

    def acquire(self):
        with self._lock:
            self._clients += 1
            needs_start = self._clients == 1
        if needs_start:
            self._start()

    def release(self):
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if self._clients > 0:
                return
            if self._stop_timer:
                self._stop_timer.cancel()
            self._stop_timer = threading.Timer(self.IDLE_TIMEOUT, self._maybe_stop)
            self._stop_timer.start()

    def _maybe_stop(self):
        with self._lock:
            if self._clients > 0:
                return
        self._stop()

    async def stream_frames(self):
        """Async generator yielding JPEG frames for a single client."""
        last_frame = None
        while True:
            self._frame_event.clear()
            frame = self._latest_frame
            if frame and frame is not last_frame:
                last_frame = frame
                yield frame
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                # No new frame in 2s — camera may have died
                if self._process is None or self._process.poll() is not None:
                    break


camera = CameraManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_in_thread(func, *args):
    return await asyncio.get_event_loop().run_in_executor(None, func, *args)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    resp = HTMLResponse((APP_DIR / "static" / "index.html").read_text())
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/status")
async def get_status():
    return plotter.status_dict()


@app.get("/api/files")
async def list_files():
    files = []
    for f in sorted(UPLOAD_DIR.glob("*.svg")):
        if f.name.startswith("_"):
            continue
        stat = f.stat()
        files.append({
            "name": f.name,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return {"files": files}


@app.post("/api/select/{filename}")
async def select_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    plotter.selected_file = filename
    return {"selected": filename}


@app.post("/api/select")
async def deselect_file():
    plotter.selected_file = None
    return {"selected": None}


@app.get("/api/files/{filename}/layers")
async def get_layers(filename: str):
    """Extract inkscape layers from an SVG file."""
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    layers = []
    try:
        for _, elem in ET.iterparse(path, events=["start"]):
            if elem.tag == '{http://www.w3.org/2000/svg}g':
                label = elem.get('{http://www.inkscape.org/namespaces/inkscape}label')
                groupmode = elem.get('{http://www.inkscape.org/namespaces/inkscape}groupmode')
                if label and groupmode == 'layer':
                    parts = label.strip().split(None, 1)
                    num = None
                    try:
                        num = int(parts[0])
                    except (ValueError, IndexError):
                        pass
                    layers.append({"label": label, "number": num})
            elem.clear()
    except Exception:
        pass
    return {"layers": layers}


@app.post("/api/estimate/{filename}")
async def estimate_plot(filename: str, request: Request):
    """Run axicli in preview mode to estimate plot time and distances."""
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    options = body.get("options", {})

    def _run():
        cmd = [AXICLI_CMD, str(path), "--preview", "--report_time"]
        if CONFIG_PATH.exists():
            cmd += ["--config", str(CONFIG_PATH)]
        for k, v in options.items():
            cmd += [f"--{k}", str(v)]
        plotter._append_log(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(UPLOAD_DIR))
        output = (result.stdout or "") + (result.stderr or "")
        info = {}
        if result.returncode != 0:
            for line in reversed(output.splitlines()):
                line = line.strip()
                if line and not line.startswith("File "):
                    info["error"] = line
                    plotter._append_log(f"ERROR: {line}")
                    break
            return info
        for line in output.splitlines():
            line = line.strip()
            kv = line.split(":", 1)
            val = kv[1].strip() if len(kv) > 1 else line
            if "Estimated print time" in line:
                info["estimated_time"] = val
            elif "Length of path" in line:
                info["path_length"] = val
            elif "Pen-up travel" in line:
                info["penup_distance"] = val
            elif "Total movement" in line or "Total distance" in line:
                info["total_distance"] = val
        if info:
            summary = [f"{k}: {v}" for k, v in info.items()]
            if summary:
                plotter._append_log("Estimate: " + ", ".join(summary))
        return info

    return await _run_in_thread(_run)


@app.post("/api/upload")
async def upload_svg(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".svg"):
        raise HTTPException(400, "Only SVG files are accepted")
    dest = UPLOAD_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"name": file.filename, "size": len(content)}


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    path.unlink()
    return {"deleted": filename}


@app.post("/api/plot/{filename}")
async def start_plot(filename: str, request: Request):
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    options = body.get("options", None)
    try:
        plotter.start_plot(str(path), options)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "started", "file": filename}


@app.post("/api/pause")
async def pause_plot():
    try:
        await _run_in_thread(plotter.pause)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "paused"}


@app.post("/api/resume")
async def resume_plot():
    try:
        await _run_in_thread(plotter.resume)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "resumed"}


@app.post("/api/stop")
async def stop_plot():
    await _run_in_thread(plotter.stop)
    return {"status": "stopped"}


@app.post("/api/pen/up")
async def pen_up():
    try:
        await _run_in_thread(plotter.pen_up)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "pen_up"}


@app.post("/api/pen/down")
async def pen_down():
    try:
        await _run_in_thread(plotter.pen_down)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "pen_down"}


@app.post("/api/move")
async def move(request: Request):
    body = await request.json()
    dx = float(body.get("dx", 0))
    dy = float(body.get("dy", 0))
    try:
        await _run_in_thread(plotter.walk, dx, dy)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "moved", "dx": dx, "dy": dy}


@app.post("/api/home")
async def go_home():
    try:
        await _run_in_thread(plotter.go_home)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "homing"}


@app.get("/api/events")
async def event_stream():
    """SSE endpoint for real-time status updates."""
    async def generate():
        last_state = None
        last_progress = None
        last_log_version = None
        while True:
            status = plotter.status_dict()
            state = status["state"]
            progress = status["progress"]
            log_ver = status["log_version"]
            if state != last_state or progress != last_progress or log_ver != last_log_version:
                yield f"data: {json.dumps(status)}\n\n"
                last_state = state
                last_progress = progress
                last_log_version = log_ver
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Camera endpoints
# ---------------------------------------------------------------------------

@app.get("/api/camera/status")
async def camera_status():
    return {"available": camera.is_available(), "command": camera._command or ""}


@app.get("/api/camera/config")
async def get_camera_config():
    return {"command": camera._command or ""}


@app.post("/api/camera/config")
async def save_camera_config(request: Request):
    body = await request.json()
    command = body.get("command", "").strip()
    camera._stop()
    _save_camera_config({"command": command})
    camera._load_command()
    return {"saved": True, "available": camera.is_available()}


@app.get("/api/camera/stream")
async def camera_stream():
    if not camera.is_available():
        raise HTTPException(404, "No camera detected")

    async def generate():
        camera.acquire()
        try:
            async for frame in camera.stream_frames():
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
        finally:
            camera.release()

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    """Return the latest JPEG frame from the running camera, or start it briefly."""
    if not camera.is_available():
        raise HTTPException(404, "No camera detected")
    camera.acquire()
    try:
        frame = camera.get_latest_frame()
        if not frame:
            try:
                await asyncio.wait_for(camera._frame_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                raise HTTPException(500, "No frame available")
            frame = camera.get_latest_frame()
        if not frame:
            raise HTTPException(500, "No frame available")
        return StreamingResponse(
            iter([frame]),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache"},
        )
    finally:
        camera.release()


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "Config file not found")
    return {"path": str(CONFIG_PATH), "content": CONFIG_PATH.read_text()}

@app.post("/api/config")
async def save_config(request: Request):
    body = await request.json()
    content = body.get("content", "")
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(content)
    return {"saved": True}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=4443,
        ssl_keyfile=os.environ.get("SSL_KEY", None),
        ssl_certfile=os.environ.get("SSL_CERT", None),
        reload=False,
    )
