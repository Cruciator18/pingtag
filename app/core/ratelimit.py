from app.core.redis import RedisClient


async def hit(redis: RedisClient, key: str, limit: int, window_s: int) -> bool:
    """Fixed-window counter. Returns True if this call is within the limit."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_s, nx=True)  # set the TTL only on the first hit
        count, _ = await pipe.execute()
    return int(count) <= limit
