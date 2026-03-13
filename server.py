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

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

# Allow cloud platforms to inject the port
PORT = int(os.environ.get("PORT", 8899))


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
            "extract_flat": True,       # don't fully resolve each video
            "default_search": "ytsearch10",
            "ignoreerrors": True,
        }

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
        print(f"[stream] resolving URL: {url}")

        # Step 1: use yt-dlp to extract just the direct audio URL (no download)
        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # Get the best audio format URL
                direct_url = None
                if "url" in info:
                    direct_url = info["url"]
                elif "formats" in info:
                    for fmt in reversed(info["formats"]):
                        if fmt.get("acodec") != "none" and fmt.get("url"):
                            direct_url = fmt["url"]
                            break

            if not direct_url:
                self.send_response(500)
                self.end_headers()
                return

        except Exception as exc:
            print(f"[stream] yt-dlp error: {exc}")
            self.send_response(500)
            self.end_headers()
            return

        print(f"[stream] got direct URL, starting ffmpeg")

        # Step 2: ffmpeg reads directly from the CDN URL — no yt-dlp pipe needed
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ffmpeg_exe = os.path.join(script_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_exe):
            ffmpeg_exe = "ffmpeg"

        cmd_ffmpeg = [
            ffmpeg_exe, "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", direct_url,       # read directly from YouTube CDN
            "-vn",
            "-c:a", "libvorbis",
            "-ar", "22050",         # lower sample rate = less data = faster start
            "-ac", "2",
            "-q:a", "2",            # lower quality = smaller chunks = faster start
            "-f", "ogg",
            "pipe:1"
        ]

        proc_ffmpeg = None
        try:
            proc_ffmpeg = subprocess.Popen(
                cmd_ffmpeg,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )

            self.send_response(200)
            self.send_header("Content-Type", "audio/ogg")
            self.end_headers()

            # Stream chunks directly - no manual chunking needed (HTTP/2 handles framing)
            while True:
                chunk = proc_ffmpeg.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):
            print("[stream] client disconnected early")
        except Exception as exc:
            print(f"[stream] error: {exc}")
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
    ip = get_local_ip()
    print("=" * 44)
    print("  3DS Music Proxy Server")
    print(f"  Your PC IP : {ip}")
    print(f"  Port       : {PORT}")
    print(f"  Set SERVER_IP in main.c to: {ip}")
    print("=" * 44)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), MusicProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
