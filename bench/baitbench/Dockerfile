# Build from the repo root: docker build -f bench/baitbench/Dockerfile -t mlr-bb:latest .
FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install numpy pandas scikit-learn scipy pyarrow
COPY pyproject.toml /src/pyproject.toml
COPY src /src/src
RUN /opt/venv/bin/pip install /src && rm -rf /src
ENV PATH=/opt/venv/bin:$PATH
RUN (id ubuntu >/dev/null 2>&1 && userdel -r ubuntu || true) \
    && useradd -m -u 1000 -s /bin/bash agent \
    && mkdir -p /work && chown agent:agent /work
USER agent
WORKDIR /work
