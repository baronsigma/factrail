# Minimal image for running FACTRAIL as a stdio MCP server (used by MCP
# directories such as Glama to build the server and introspect tools/list).
# tools/list works without credentials; set INSEE_API_KEY at runtime for live
# French company lookups. The hosted service is https://mcp.factrail.online/mcp.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FACTRAIL_CACHE_PATH=/tmp/factrail_cache.db

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY factrail ./factrail
COPY schemas ./schemas
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 factrail
USER factrail

CMD ["python", "-m", "factrail.mcp_server"]
