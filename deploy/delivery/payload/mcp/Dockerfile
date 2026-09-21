FROM python:3.12-slim

# psql 客户端：MCP 用 `psql` 直连 Postgres（容器部署不做 docker exec；见 ke_db 的 BODHI_DB_HOST 分支）
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 期望的包内布局（与 server.py 的 REPO=parents[2] 假设一致）：
#   tools/ontology-mcp tools/ke-core tools/ontology-extract skills artifacts/weknora ontology logs .env
COPY tools/ /app/tools/
COPY skills/ /app/skills/
COPY artifacts/ /app/artifacts/
COPY ontology/ /app/ontology/
COPY .env /app/.env
RUN mkdir -p /app/logs

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/bodhi/ontology/classes',timeout=3).status==200 else 1)"

CMD ["python3", "tools/ontology-mcp/server.py", "--host", "0.0.0.0", "--port", "8765"]
