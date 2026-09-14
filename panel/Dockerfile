# The blind panel's server, as one Cloud Run service (deploy/cloud-run.sh).
#
# The image holds CODE ONLY. What changes with an export - items.json, the
# private key - and the votes file the server appends to live in a Cloud
# Storage bucket mounted at /data, so a re-export is a file sync, not a
# rebuild, and a vote survives a restart or a new revision. The pairs' media
# is not here either: the studies console serves it (PANEL_MEDIA_BASE).
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY runner ./runner
COPY site ./site
RUN pip install --no-cache-dir . && rm -rf /root/.cache
ENV PANEL_DATA=/data PYTHONUNBUFFERED=1
# Cloud Run injects PORT; the server binds every interface behind Cloud Run's
# own HTTPS front. The key sits OUTSIDE dist/ - the server refuses otherwise.
CMD ["sh", "-c", "exec python -m runner.cli serve --host 0.0.0.0 --port ${PORT:-8080} --dist ${PANEL_DATA}/dist --key ${PANEL_DATA}/private/key.json --votes ${PANEL_DATA}/votes.jsonl"]
