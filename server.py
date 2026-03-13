#!/usr/bin/env python3
"""
3DS Music Proxy Server
Searches YouTube via yt-dlp and streams full songs as OGG Vorbis to the 3DS.

Requirements:
    pip install yt-dlp
    ffmpeg must be on your PATH (https://ffmpeg.org/download.html)

Run:
    python server.py
Then note the IP printed and set SERVER_IP in main.c to match.
"""

import http.server
import urllib.parse
import subprocess
import json
import socket
import sys
import os
import threading
import tempfile
import base64

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

# Allow cloud platforms to inject the port
PORT = int(os.environ.get("PORT", 8899))

# Write cookies from env var to a temp file (safe — never stored in repo)
COOKIES_FILE = None
_cookies_b64 = os.environ.get("YOUTUBE_COOKIES_B64", "")
if _cookies_b64:
    try:
        _data = base64.b64decode(_cookies_b64)
        _tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb")
        _tmp.write(_data)
        _tmp.close()
        COOKIES_FILE = _tmp.name
        print(f"[auth] Successfully loaded {len(_data)} bytes of cookies from env")
    except Exception as e:
        print(f"[auth] ERROR decoding YOUTUBE_COOKIES_B64: {e}")
elif os.path.exists("cookies.txt"):  # fallback for local dev
    COOKIES_FILE = "cookies.txt"
    print(f"[auth] Using local cookies.txt")
else:
    print("[auth] WARNING: No YouTube cookies found. Streams may fail on cloud.")


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class MusicProxyHandler(http.server.BaseHTTPRequestHandler):

    # ------------------------------------------------------------------ #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/search":
            self._handle_search(params)
        elif parsed.path == "/stream":
            self._handle_stream(params)
        else:
            self.send_response(404)
            self.end_headers()

    # ------------------------------------------------------------------ #
    def _handle_search(self, params):
        query = params.get("q", [""])[0].strip()
        if not query:
            self._bad_request()
            return

        print(f"[search] {query}")

        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "default_search": "ytsearch10",
            "ignoreerrors": True,
        }
        if COOKIES_FILE:
            ydl_opts["cookiefile"] = COOKIES_FILE

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)

            results = []
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                vid_id = entry.get("id", "")
                title  = (entry.get("title") or "Unknown")[:50]
                artist = (entry.get("uploader") or "Unknown")[:30]
                dur    = int(entry.get("duration") or 0)
                duration = f"{dur // 60}:{dur % 60:02d}" if dur else "?:??"
                results.append({"id": vid_id, "title": title,
                                 "artist": artist, "duration": duration})
                if len(results) >= 10:
                    break

            body = json.dumps(results, separators=(',', ':')).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print(f"[search] returned {len(results)} results")
            self.close_connection = True

        except Exception as exc:
            print(f"[search] ERROR: {exc}")
            self.send_response(500)
            self.end_headers()
            self.close_connection = True

    # ------------------------------------------------------------------ #
    def _handle_stream(self, params):
        video_id = params.get("id", [""])[0].strip()
        if not video_id:
            self._bad_request()
            return

        url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[stream] starting: {url}")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        ffmpeg_exe = os.path.join(script_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_exe):
            ffmpeg_exe = "ffmpeg"

        # Use yt-dlp to get direct URL first (no download, just metadata)
        direct_url = None
        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "quiet":       True,
                "no_warnings": True,
            }
            if COOKIES_FILE:
                ydl_opts["cookiefile"] = COOKIES_FILE
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # Do NOT call sanitize_info — it strips the CDN URLs!
                # Try top-level url first (already-selected format)
                direct_url = info.get("url")
                # Walk formats if not found at top level
                if not direct_url:
                    for fmt in reversed(info.get("formats", [])):
                        if fmt.get("url") and fmt.get("acodec", "none") != "none":
                            direct_url = fmt["url"]
                            break

        except Exception as exc:
            print(f"[stream] yt-dlp direct URL extraction failed: {exc}")
            # If the specific "format not available" error happens here, the fallback might still fail.
            # But we try anyway.

        if not direct_url:
            print("[stream] no direct URL found, falling back to yt-dlp pipe")
            # Fallback: pipe yt-dlp stdout into ffmpeg stdin
            cmd_ytdlp = [
                sys.executable, "-m", "yt_dlp",
                "-f", "bestaudio/best",
                "--quiet", "--no-warnings",
                "--no-check-certificates",
                "-o", "-",
                url
            ]
            if COOKIES_FILE:
                cmd_ytdlp.insert(3, "--cookiefile")
                cmd_ytdlp.insert(4, COOKIES_FILE)
            cmd_ffmpeg = [
                ffmpeg_exe, "-y",
                "-i", "pipe:0",
                "-vn", "-c:a", "libvorbis",
                "-ar", "22050", "-ac", "2", "-q:a", "2",
                "-f", "ogg", "pipe:1"
            ]
            proc_ytdlp = None
            proc_ffmpeg = None
            try:
                proc_ytdlp = subprocess.Popen(cmd_ytdlp, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                proc_ffmpeg = subprocess.Popen(cmd_ffmpeg, stdin=proc_ytdlp.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                if proc_ytdlp.stdout:
                    proc_ytdlp.stdout.close()
                self.send_response(200)
                self.send_header("Content-Type", "audio/ogg")
                self.end_headers()
                while True:
                    chunk = proc_ffmpeg.stdout.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                print("[stream] client disconnected")
            except Exception as exc:
                print(f"[stream] pipe error: {exc}")
            finally:
                if proc_ytdlp: proc_ytdlp.terminate(); proc_ytdlp.wait()
                if proc_ffmpeg: proc_ffmpeg.terminate(); proc_ffmpeg.wait()
            print("[stream] done (pipe fallback)")
            return

        # Fast path: ffmpeg reads directly from CDN URL
        print(f"[stream] got direct URL, starting ffmpeg fast path")
        cmd_ffmpeg = [
            ffmpeg_exe, "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", direct_url,
            "-vn", "-c:a", "libvorbis",
            "-ar", "22050", "-ac", "2", "-q:a", "2",
            "-f", "ogg", "pipe:1"
        ]
        proc_ffmpeg = None
        try:
            proc_ffmpeg = subprocess.Popen(cmd_ffmpeg, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.send_response(200)
            self.send_header("Content-Type", "audio/ogg")
            self.end_headers()
            while True:
                chunk = proc_ffmpeg.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            print("[stream] client disconnected early")
        except Exception as exc:
            print(f"[stream] ffmpeg error: {exc}")
        finally:
            if proc_ffmpeg:
                proc_ffmpeg.terminate()
                proc_ffmpeg.wait()
        print("[stream] done")


    def _bad_request(self):
        self.send_response(400)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        pass


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    # Log environment info for debugging
    print("=" * 44)
    print("  3DS Music Proxy Server")
    try:
        import yt_dlp
        print(f"  yt-dlp version: {yt_dlp.version.__version__}")
    except Exception:
        print("  yt-dlp version: unknown")
    
    node_ver = subprocess.getoutput("node --version")
    print(f"  Node.js version: {node_ver}")
    
    ip = get_local_ip()
    print(f"  Your PC IP : {ip}")
    print(f"  Port       : {PORT}")
    print("=" * 44)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), MusicProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
