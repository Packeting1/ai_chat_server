FROM python:3.11-slim

WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

# 添加 bullseye 源并安装所有依赖包
RUN echo "deb http://deb.debian.org/debian bullseye main" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --fix-missing --no-install-recommends \
    bash \
    procps \
    coreutils \
    libopenblas0 \
    libssl3 \
    libssl1.1 \
    ca-certificates \
    build-essential \
    wget \
    git \
    git-lfs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install modelscope funasr torch torchaudio

EXPOSE 10095

CMD ["bash", "-c", "bash FunASR/runtime/run_server_2pass.sh && tail -f /dev/null"]
