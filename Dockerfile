FROM python:3.12.9-slim-bookworm

# Install ffmpeg + opus (required for voice recording)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libopus0 \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /bot
COPY requirements.txt /bot/
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . /bot

# Default paths inside container — override via environment or docker-compose
ENV DB_PATH=/bot/data/database.db
ENV RECORDINGS_PATH=/bot/recordings
ENV CLIPS_PATH=/bot/clips

# Create volume mount points
RUN mkdir -p /bot/data /bot/recordings /bot/clips

ENTRYPOINT ["python", "-u", "bot.py"]
