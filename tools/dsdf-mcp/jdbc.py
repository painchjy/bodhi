"""Independent JDBC access for the DSDF PostgreSQL service."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class JdbcConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    driver_class: str
    jdbc_jar: str
    service_id: str
    service_name: str
    scope: str

    @property
    def jdbc_url(self) -> str:
        return "jdbc:postgresql://%s:%d/%s" % (self.host, self.port, self.database)

    def service(self) -> dict[str, str]:
        return {"service_id": self.service_id, "service_name": self.service_name,
                "database_type": "postgresql", "scope": self.scope}


def load_config(environ: dict[str, str] | None = None) -> JdbcConfig:
    env = os.environ if environ is None else environ
    raw_port = str(env.get("DSDF_DB_PORT", "5432")).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("DSDF_DB_PORT 必须是 1 到 65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError("DSDF_DB_PORT 必须是 1 到 65535 的整数")
    return JdbcConfig(
        host=str(env.get("DSDF_DB_HOST", "")).strip(),
        port=port,
        database=str(env.get("DSDF_DB_NAME", "")).strip(),
        user=str(env.get("DSDF_DB_USER", "")).strip(),
        password=str(env.get("DSDF_DB_PASSWORD", "")),
        driver_class=str(env.get("DSDF_JDBC_DRIVER", "org.postgresql.Driver")).strip(),
        jdbc_jar=str(env.get("DSDF_JDBC_JAR", "")).strip(),
        service_id=str(env.get("DSDF_SERVICE_ID", "dsdf")).strip() or "dsdf",
        service_name=str(env.get("DSDF_SERVICE_NAME", "DSDF DWS")).strip() or "DSDF DWS",
        scope=str(env.get("DSDF_SERVICE_SCOPE", "")).strip(),
    )


def validate_config(config: JdbcConfig) -> None:
    required = {
        "DSDF_DB_HOST": config.host,
        "DSDF_DB_NAME": config.database,
        "DSDF_DB_USER": config.user,
        "DSDF_DB_PASSWORD": config.password,
        "DSDF_JDBC_JAR": config.jdbc_jar,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError("缺少 DSDF JDBC 配置：" + ", ".join(missing))
    if not Path(config.jdbc_jar).is_file():
        raise ValueError("DSDF_JDBC_JAR 文件不存在：%s" % config.jdbc_jar)
    if not config.driver_class:
        raise ValueError("DSDF_JDBC_DRIVER 不能为空")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def query(sql: str, params: Sequence[Any] = (),
          connector: Callable[..., Any] | None = None) -> list[dict[str, str]]:
    """Run one statement in a JDBC read-only transaction and return psql-like strings."""
    config = load_config()
    validate_config(config)
    if connector is None:
        try:
            import jaydebeapi
        except ImportError as exc:
            raise RuntimeError("缺少 JayDeBeApi，请安装 requirements.txt 中的依赖") from exc
        connector = jaydebeapi.connect

    conn = connector(config.driver_class, config.jdbc_url,
                     [config.user, config.password], jars=[config.jdbc_jar])
    cursor = None
    try:
        jdbc_connection = getattr(conn, "jconn", None)
        if jdbc_connection is not None:
            jdbc_connection.setReadOnly(True)
            jdbc_connection.setAutoCommit(False)
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(sql, tuple(params))
        description = cursor.description or ()
        names = [str(column[0]) for column in description]
        return [{name: _text(value) for name, value in zip(names, row)}
                for row in cursor.fetchall()]
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
