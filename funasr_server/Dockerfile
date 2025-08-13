FROM python:3.11-slim

WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --fix-missing --no-install-recommends \
    ca-certificates \
    git \
    git-lfs \
    procps \
    libssl1.1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && \
    pip install modelscope funasr torch torchaudio

EXPOSE 10095