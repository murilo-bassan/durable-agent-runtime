FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY runtime ./runtime
COPY web ./web
COPY scripts ./scripts
RUN useradd --create-home --uid 10001 runtime
USER runtime

EXPOSE 8700
# Same image, two roles: `api` (default) or `worker` (command override in compose).
CMD ["python", "-m", "uvicorn", "runtime.api:app", "--host", "0.0.0.0", "--port", "8700"]
