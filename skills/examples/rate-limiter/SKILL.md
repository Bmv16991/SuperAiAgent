---
name: rate-limiter
description: "Generate rate limiting code for Python APIs using slowapi, Redis, or in-memory approaches"
tags: [python, fastapi, api, rate-limit, backend]
---

## Purpose
Generates production-ready rate limiting implementations for Python web APIs.
Supports FastAPI (slowapi), Flask, and generic Redis-based patterns.

## Code
```python
def run(task: str, context: str = "") -> str:
    task_lower = task.lower()

    if "redis" in task_lower:
        return '''# Redis-based rate limiter
import redis
import time

r = redis.Redis(host="localhost", port=6379, db=0)

def is_rate_limited(user_id: str, limit: int = 10, window: int = 60) -> bool:
    key = f"rate:{user_id}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    count, _ = pipe.execute()
    return count > limit
'''

    # Default: FastAPI + slowapi
    return '''# FastAPI rate limiter with slowapi
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/api/data")
@limiter.limit("10/minute")
async def get_data(request: Request):
    return {"data": "ok"}

# Install: pip install slowapi
'''
```
