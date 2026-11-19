FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHAINTRACE_CACHE=/cache/cache.sqlite
COPY pyproject.toml README.md ./
COPY chaintrace ./chaintrace
COPY web ./web
COPY data ./data
RUN pip install --no-cache-dir -e . && useradd --create-home app && mkdir -p /cache && chown app /cache
USER app
EXPOSE 8000
CMD ["chaintrace", "serve", "--host", "0.0.0.0", "--port", "8000"]
