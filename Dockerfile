FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_TRANSPORT=streamable-http

# Railway sets PORT automatically; fallback to 8000 locally
ENV MCP_PORT=8000

CMD ["python", "server.py"]
