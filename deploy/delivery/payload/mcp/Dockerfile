FROM python:3.12-slim

# 构建参数：内网/受限网络下把包源换成你们的镜像（**实测公网 Debian 源可能不通**）
#   docker build --build-arg APT_MIRROR=mirrors.aliyun.com -t bodhi2-mcp:1.0 .
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# psql 客户端：MCP 用 `psql` 直连 Postgres（容器部署不做 docker exec；见 ke_db 的 BODHI_DB_HOST 分支）
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 期望的包内布局（与 server.py 的 REPO=parents[2] 假设一致，**02 包解出来就是这个形状**）：
#   tools/ artifacts/ skills/ logs/ ontology/uploads/ .env
# 注：本体真源 TTL 在 03-manual 包里（服务运行不需要）；artifacts/ 里的编译产物是运行时索引。
COPY tools/ /app/tools/
COPY skills/ /app/skills/
COPY artifacts/ /app/artifacts/
COPY ontology/ /app/ontology/
# 用模板作为容器内默认配置（BODHI_DB_* 由 compose 的 env_file/环境变量覆盖）
COPY .env.example /app/.env
RUN mkdir -p /app/logs

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/bodhi/ontology/classes',timeout=3).status==200 else 1)"

CMD ["python3", "tools/ontology-mcp/server.py", "--host", "0.0.0.0", "--port", "8765"]
