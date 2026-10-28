"""FastAPI dependencies. Heavy clients are imported inside the function, never at module
level: /health/live must work (and import) without sqlalchemy or redis."""
from datetime import UTC, datetime
from functools import lru_cache

from app import settings
from app.services.health import HealthProbes


@lru_cache(maxsize=1)
def get_probes() -> HealthProbes:
    # Tests replace this with app.dependency_overrides[get_probes].
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.health_probes import SqlRedisProbes

    db_url, redis_url = settings.database_url(), settings.redis_url()
    # Short timeouts: a dead dependency must answer "down" quickly, not hold the probe open.
    # asyncpg's `timeout` bounds connect; `command_timeout` bounds each query.
    engine = (
        create_async_engine(
            db_url, pool_pre_ping=True, connect_args={"timeout": 2, "command_timeout": 2}
        )
        if db_url
        else None
    )
    redis = (
        Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1) if redis_url else None
    )
    return SqlRedisProbes(engine, redis)


def get_now() -> datetime:
    return datetime.now(UTC)
