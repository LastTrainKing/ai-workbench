# AI Workbench — standalone worker image.
# Reuses the backend image so the API and the worker can never drift apart.
#
# Build context is the repository root:
#   docker build -f worker/Dockerfile -t ai-workbench-worker .

FROM ai-workbench-backend:latest

# The worker does not expose a port; it only polls the job table.
ENV WORKER_ENABLED=false

# MIGRATE_ON_START defaults to false: the API service owns schema migrations,
# and two containers racing to migrate is a classic production incident.
CMD ["sh", "-c", "if [ \"$MIGRATE_ON_START\" = \"true\" ]; then python -m alembic upgrade head; fi; python -m app.cli worker --concurrency ${WORKER_CONCURRENCY:-2}"]
