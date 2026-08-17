FROM docker.io/library/python:3.13.14-slim-bookworm@sha256:de572b33eae61a53675a87bbd02b5e365df7b6b2b06c9276124e965cec08c452

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 65532 app \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --chown=65532:65532 server.py /app/server.py
USER 65532:65532
ENTRYPOINT ["python3", "/app/server.py"]
