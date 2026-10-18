# DarkGlass API + MCP agent. Build from the repository root:
#   docker build -f backend/Dockerfile -t darkglass-backend .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# The API imports the MCP tools from mcp_server/ and builds the graph from data/ on first start.
COPY backend backend
COPY mcp_server mcp_server
COPY data data

RUN useradd --create-home darkglass && chown -R darkglass /app
USER darkglass
WORKDIR /app/backend
EXPOSE 8010
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers"]
