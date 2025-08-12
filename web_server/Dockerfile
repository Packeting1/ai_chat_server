FROM python:3.13-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    gcc \
    libasound2-dev \
    portaudio19-dev \
    ffmpeg \
    git-lfs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p staticfiles && \
    mkdir -p /workspace/data && \
    chmod -R 755 /workspace

EXPOSE 8000
EXPOSE 8443

CMD ["bash", "-c", "bash start.sh && tail -f /dev/null"]