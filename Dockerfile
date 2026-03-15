FROM python:3.11-slim

# Install curl + ca-certs first so we can fetch NodeSource setup script
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS from NodeSource (yt-dlp requires a modern JS runtime)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Always install the latest yt-dlp to avoid YouTube signature issues
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade yt-dlp

COPY server.py .

# Run the server
CMD ["python", "server.py"]
