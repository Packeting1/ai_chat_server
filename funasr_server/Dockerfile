FROM python:3.11-slim

WORKDIR /workspace

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --fix-missing --no-install-recommends \
    ca-certificates \
    git \
    git-lfs \
    procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install modelscope funasr torch torchaudio

RUN chmod +x /workspace/run_server_2pass.sh /workspace/parse_options.sh

EXPOSE 10095

CMD ["bash", "-c", "bash run_server_2pass.sh && tail -f /dev/null"]
