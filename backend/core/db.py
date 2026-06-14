"""
core/db.py

Thin async wrapper for the synchronous supabase-py client.

supabase-py (and the underlying postgrest-py) is a synchronous library.
Calling it directly from async FastAPI route handlers blocks the event loop,
freezing the server for all other requests until the DB call completes.

run_db() offloads any synchronous Supabase query to a thread pool executor
so the event loop stays free. This is the correct pattern for integrating
sync I/O libraries into an async application.

Usage:
    from core.db import run_db
    from core.config import get_supabase

    result = await run_db(
        get_supabase().table("profiles").select("*").eq("user_id", uid)
    )
    # result is the .execute() return value

The callable form also works for multi-step operations:
    result = await run_db(lambda: get_supabase().table(...).upsert(...).execute())
"""

import asyncio
from typing import Any, Callable


async def run_db(query_or_callable: Any) -> Any:
    """
    Runs a synchronous Supabase query (or any callable) in a thread pool.

    Accepts either:
    - A postgrest QueryBuilder (anything with an .execute() method)
    - A plain callable (lambda or function) that performs the DB work

    Returns the result of .execute() or the callable's return value.
    """
    loop = asyncio.get_event_loop()

    if callable(query_or_callable) and not hasattr(query_or_callable, 'execute'):
        # It's a plain callable (e.g. a lambda)
        return await loop.run_in_executor(None, query_or_callable)
    else:
        # It's a QueryBuilder — call .execute() in the thread pool
        return await loop.run_in_executor(None, query_or_callable.execute)
