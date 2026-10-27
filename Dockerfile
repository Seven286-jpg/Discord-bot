FROM python:3.12-slim

# FFmpeg + libopus are required for voice/music
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY dreams-rp-bot/dreams-rp-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dreams-rp-bot/dreams-rp-bot/ .

CMD ["python", "bot.py"]
