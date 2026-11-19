"""字幕人工审核页面。

- --serve（推荐）：本地起一个只监听 127.0.0.1 的小服务，保存直接写回 SRT；
  点“审核完成”后服务自动退出、命令返回 —— Agent 可以阻塞等待这一步。
- 不加 --serve：生成静态 review.html，保存时下载 SRT，手动覆盖。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .subtitles import parse_srt

TEMPLATE = Path(__file__).with_name("review_template.html")


def render_html(video: Path, srt: Path, video_url: str, max_chars: float, reviewed: bool) -> str:
    data = {"name": video.name, "video": video_url, "srt_name": srt.name, "max_chars": max_chars,
            "reviewed": reviewed, "cues": parse_srt(srt.read_text(encoding="utf-8-sig"))}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)


def write_static(video: Path, srt: Path, out: Path, max_chars: float, reviewed: bool) -> Path:
    try:
        url = quote(Path(os.path.relpath(video, out.parent)).as_posix())
    except ValueError:  # Windows 跨盘符
        url = video.resolve().as_uri()
    out.write_text(render_html(video, srt, url, max_chars, reviewed), encoding="utf-8")
    return out


def serve(video: Path, srt: Path, max_chars: float, on_finish, port: int = 0, open_browser: bool = True) -> dict:
    result = {"finished": False}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                html = render_html(video, srt, "/video", max_chars, result["finished"])
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/video":
                return self._video()
            return self._send(404, b"not found")

        def _video(self):
            size = video.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                if a:
                    start, end = int(a), (int(b) if b else size - 1)
                else:
                    start = size - int(b)
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/quicktime" if video.suffix.lower() == ".mov" else "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            try:
                with open(video, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/srt":
                return self._send(404, b"not found")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
            if not parse_srt(body):
                return self._send(400, "SRT 为空或格式不对".encode())
            if srt.exists():
                shutil.copyfile(srt, srt.with_suffix(".prev.srt"))
            srt.write_text(body, encoding="utf-8")
            self._send(200, b"ok")
            if parse_qs(u.query).get("finish"):
                result["finished"] = True
                on_finish()
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(json.dumps({"event": "review_server_started", "url": url}, ensure_ascii=False), file=sys.stderr, flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    result["url"] = url
    return result
