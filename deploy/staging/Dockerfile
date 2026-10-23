# Build ONLY from reviewed main with an administrator-approved pinned base.
# Do not use a feature Dockerfile. Supply PYTHON_BASE=python:3.13-slim@sha256:<verified digest>.
ARG PYTHON_BASE
FROM ${PYTHON_BASE}
RUN apt-get update && apt-get install -y --no-install-recommends git bash util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /approved-requirements.txt
RUN python -m pip install --no-cache-dir -r /approved-requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
