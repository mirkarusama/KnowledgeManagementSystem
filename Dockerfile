# One small Python image runs every backend service (agent API, MCP server,
# ingestion, mock Atlassian, offline demo model) - saves disk space and build time.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY kms ./kms
COPY tests ./tests
COPY eval ./eval
ARG EXTRAS=""
RUN pip install --upgrade pip && pip install ".${EXTRAS}"

RUN useradd --create-home --uid 10001 kms
USER kms
EXPOSE 8000
CMD ["uvicorn", "kms.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
