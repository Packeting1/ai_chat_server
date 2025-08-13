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

RUN wget http://archive.debian.org/debian/pool/main/o/openssl/libssl1.1_1.1.1w-0+deb11u1_amd64.deb \
    && dpkg -i libssl1.1_1.1.1w-0+deb11u1_amd64.deb || apt-get install -f -y \
    && rm -f libssl1.1_1.1.1w-0+deb11u1_amd64.deb

RUN pip install --upgrade pip && \
    pip install modelscope funasr torch torchaudio

EXPOSE 10095