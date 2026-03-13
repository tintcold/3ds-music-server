FROM python:3.11-slim

# Install ffmpeg + Node.js (required for yt-dlp JavaScript challenge solver)
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    ca-certificates && \
    ( [ -e /usr/bin/node ] || ln -s /usr/bin/nodejs /usr/bin/node ) && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Always install the latest yt-dlp to avoid YouTube signature issues
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade yt-dlp

COPY server.py .

# Run the server
CMD ["python", "server.py"]
