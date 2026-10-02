# AI Workbench — backend image (API + in-process worker)
#
# The same image is used for the standalone worker service with a different
# command, so the two always share an identical dependency set.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- dependencies (own layer so code edits do not re-install) ---------------
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# --- application code -------------------------------------------------------
COPY backend/ ./backend/

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.runtime/storage/uploads \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app/backend \
    STORAGE_DIR=/app/.runtime/storage

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

# Apply migrations, then serve. `alembic upgrade head` is idempotent.
CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.cli serve --host 0.0.0.0 --port 8000"]
