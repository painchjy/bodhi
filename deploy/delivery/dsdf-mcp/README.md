# dsdf-mcp 独立服务部署

`dsdf-mcp` 使用自己的只读 JDBC 连接，不依赖 Bodhi `ke-core`、WeKnora PG 或 `psql`。默认监听 `0.0.0.0:8772`。

## WSL 本机启动

在 WSL 中准备 `/etc/dsdf-mcp.env` 和 JDBC JAR 后，可单独启动 DSDF，不重启其它 MCP 服务：

```bash
sudo bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh dsdf
```

`wsl-up.sh up` 在 DSDF 环境文件存在时也会启动该服务；`status`、`logs` 和 `down` 会包含 DSDF 服务。启动器通过 systemd 运行 `bodhi2-dsdf-mcp.service`，并执行一次只读 JDBC 探测。数据库口令只从 `/etc/dsdf-mcp.env` 读取，不应写入命令行或日志。

旧 `dbbi_data` gateway 已从本项目移除；如果部署主机仍有旧单元，请先停用以释放 8772：

```bash
sudo systemctl disable --now bodhi-mcp-gateway
```

## 依赖

- Bodhi 服务 Python 环境 `/opt/bodhi-venv/bin/python3`
- Java/JVM
- pip 可访问 JayDeBeApi 1.2.3（或内部 wheel 镜像）
- 现场提供的 PostgreSQL JDBC 42.5.1 JAR
- `rsync`、`curl`、systemd

JAR、DWS 凭据和私有连接信息不随交付包发布。

## 安装

1. 将包解压到临时目录，编辑 `deploy/delivery/dsdf-mcp/dsdf-mcp.env.example`，填写 `DSDF_DB_*`，设置正确的 JDBC JAR 路径。默认路径 `/opt/dsdf-mcp/lib/postgresql-42.5.1.jar`；把现场 JAR 安装到该位置并设为 `root:dsdf-mcp`、权限 `0640`。
2. 以 root 执行：

   ```bash
   sudo bash deploy/delivery/dsdf-mcp/install.sh
   ```

3. 安装器会在首次运行时复制环境模板到 `/etc/dsdf-mcp.env` 并退出。编辑该文件后再次运行安装器；安装器在 `/opt/bodhi-venv` 安装 JayDeBeApi，验证配置和 JVM/JDBC 依赖，再启用服务。
4. 验证服务：

   ```bash
   curl -fsS http://127.0.0.1:8772/health
   ```

## WeKnora 注册

新增一个 `mcp_services` 服务，名称可使用 `dsdf_mcp`，URL 为：

```text
http://host.docker.internal:8772/mcp
```

若容器不能解析 `host.docker.internal`，使用宿主机 IP 并加入 WeKnora SSRF 白名单。

## 自检与排错

```bash
python3 deploy/delivery/dsdf-mcp/selfcheck.py \
  --env-file /etc/dsdf-mcp.env \
  --python /opt/bodhi-venv/bin/python3
journalctl -u dsdf-mcp -n 80
```

自检只验证配置、Jar 文件及驱动运行时，不执行 DWS 查询。MCP `/health` 也不需要发起数据库连接。

## 回滚

```bash
sudo systemctl disable --now dsdf-mcp
```

WeKnora 中移除或停用 `dsdf_mcp` 服务；DWS 数据库本身不会被修改。
