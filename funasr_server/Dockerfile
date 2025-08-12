FROM python:3.11-slim

WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --fix-missing \
    bash \
    procps \
    coreutils \
    libopenblas0 \
    libssl3 \
    ca-certificates \
    build-essential \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN echo "deb http://deb.debian.org/debian bullseye main" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --fix-missing libssl1.1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install modelscope funasr torch torchaudio

EXPOSE 10095

CMD ["bash", "-c", "bash FunASR/runtime/run_server_2pass.sh && tail -f /dev/null"]
