FROM python:3.11-slim

WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --fix-missing --no-install-recommends \
    ca-certificates \
    git \
    git-lfs \
    procps \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN echo "deb http://deb.debian.org/debian oldstable main" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libssl1.1 \
    && sed -i '/oldstable/d' /etc/apt/sources.list \
    && apt-get update

RUN pip install --upgrade pip && \
    pip install modelscope funasr torch torchaudio

EXPOSE 10095