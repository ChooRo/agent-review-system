"""PostgreSQL 连接与事务基础设施。

连接池供 FastAPI 同步端点所在的线程池共享；事务统一使用
REPEATABLE READ + 按集合的 advisory 事务锁，把 JSON 时代"进程内 RLock +
整文件原子替换"升级为跨进程同样成立的"读-改-写按集合串行化"，并发写不丢更新。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

from psycopg import Connection, IsolationLevel, errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings

T = TypeVar("T")

_pool: ConnectionPool | None = None


class ConcurrentWriteError(RuntimeError):
    """REPEATABLE READ 快照冲突：多个请求同时改同一集合，重试后仍失败。"""


def run_with_retry(operation: Callable[[], T], attempts: int = 3) -> T:
    """序列化冲突时按指数退避重试整个读-改-写；耗尽后抛 ConcurrentWriteError。"""
    for attempt in range(attempts):
        try:
            return operation()
        except errors.SerializationFailure:
            if attempt == attempts - 1:
                raise ConcurrentWriteError("并发写冲突，请重试") from None
            time.sleep(0.02 * (attempt + 1))
    raise ConcurrentWriteError("并发写冲突，请重试")  # 不可达，类型收窄用


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError("STORAGE_BACKEND=postgres 需要配置 DATABASE_URL")
        _pool = ConnectionPool(url, min_size=1, max_size=10, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def lock_table(conn: Connection, name: str) -> None:
    """对单个业务集合加事务级 advisory 锁，串行化该集合的读-改-写。"""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (name,))


@contextmanager
def transaction() -> Iterator[Connection]:
    """打开一个 REPEATABLE READ 事务；退出上下文时提交，异常时回滚。"""
    with get_pool().connection() as conn:
        conn.row_factory = dict_row
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        with conn.transaction():
            yield conn
