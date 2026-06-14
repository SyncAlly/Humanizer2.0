"""
core/rate_limiter.py

Per-user rate limiting backed by Supabase.

Strategy: sliding window per hour.
- Each user gets MAX_REQUESTS_PER_HOUR humanize calls per hour.
- The window resets on a rolling basis from the user's first request
  in that window, not at a fixed clock boundary.
- On every request: count rows for this user in the last hour.
  If count >= limit, reject with 429. Otherwise insert a new row.
- Old rows are cleaned up lazily (deleted when a new request comes in).

Why Supabase instead of Redis:
- No extra infrastructure needed — Supabase Postgres is already the DB.
- For this traffic volume (hundreds of users, not millions), a DB query
  per request is perfectly fast enough.
- If the app scales to need Redis, this module is the only thing to swap.

Table used (created in README setup SQL):
    rate_limits (user_id, window_start, request_count)
"""

from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
from core.config import get_supabase
from core.db import run_db

# Limits — adjust these to match your Gemini free tier budget
MAX_REQUESTS_PER_HOUR: int = 20   # per user per hour
MAX_REQUESTS_PER_DAY: int  = 100  # per user per day (soft ceiling)

WINDOW_HOURS: int = 1
WINDOW_DAYS: int  = 1


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def check_and_increment(user_id: str) -> dict:
    """
    Checks if the user is within their rate limit and records the request.

    Args:
        user_id: The authenticated user's UUID from Supabase.

    Returns:
        dict with keys: requests_this_hour, requests_today,
                        limit_per_hour, limit_per_day,
                        remaining_this_hour

    Raises:
        HTTPException 429: If the user has exceeded their hourly limit.
        HTTPException 503: If the rate limit DB query fails.
    """
    supabase = get_supabase()
    now = _now_utc()
    one_hour_ago = now - timedelta(hours=WINDOW_HOURS)
    one_day_ago  = now - timedelta(days=WINDOW_DAYS)

    try:
        # ── Count requests in the last hour ──────────────────────
        hourly_result = await run_db(
            supabase.table("rate_limits")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .gte("window_start", one_hour_ago.isoformat())
        )
        hourly_count = hourly_result.count or 0

        # ── Count requests in the last day ───────────────────────
        daily_result = await run_db(
            supabase.table("rate_limits")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .gte("window_start", one_day_ago.isoformat())
        )
        daily_count = daily_result.count or 0

        # ── Enforce hourly limit ──────────────────────────────────
        if hourly_count >= MAX_REQUESTS_PER_HOUR:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "message": f"You have used {hourly_count}/{MAX_REQUESTS_PER_HOUR} "
                               f"rewrites this hour. Limit resets on a rolling basis.",
                    "requests_this_hour": hourly_count,
                    "limit_per_hour": MAX_REQUESTS_PER_HOUR,
                    "retry_after_seconds": 3600,
                },
            )

        # ── Enforce daily limit ───────────────────────────────────
        if daily_count >= MAX_REQUESTS_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Daily limit exceeded",
                    "message": f"You have used {daily_count}/{MAX_REQUESTS_PER_DAY} "
                               f"rewrites today. Limit resets after 24 hours.",
                    "requests_today": daily_count,
                    "limit_per_day": MAX_REQUESTS_PER_DAY,
                    "retry_after_seconds": 86400,
                },
            )

        # ── Record this request ───────────────────────────────────
        await run_db(
            supabase.table("rate_limits").insert({
                "user_id":       user_id,
                "window_start":  now.isoformat(),
                "request_count": 1,
            })
        )

        # ── Clean up old rows (lazy GC) ───────────────────────────
        two_days_ago = now - timedelta(days=2)
        try:
            await run_db(
                supabase.table("rate_limits").delete()
                .eq("user_id", user_id)
                .lt("window_start", two_days_ago.isoformat())
            )
        except Exception:
            pass  # cleanup failure is not worth surfacing

        return {
            "requests_this_hour":  hourly_count + 1,
            "requests_today":      daily_count + 1,
            "limit_per_hour":      MAX_REQUESTS_PER_HOUR,
            "limit_per_day":       MAX_REQUESTS_PER_DAY,
            "remaining_this_hour": MAX_REQUESTS_PER_HOUR - (hourly_count + 1),
        }

    except HTTPException:
        raise  # Re-raise rate limit exceptions as-is

    except Exception as e:
        # DB failure — fail open (allow the request) rather than blocking
        # users due to an infra issue, but log the error.
        print(f"[rate_limiter] DB error for user {user_id}: {e}")
        return {
            "requests_this_hour":  0,
            "requests_today":      0,
            "limit_per_hour":      MAX_REQUESTS_PER_HOUR,
            "limit_per_day":       MAX_REQUESTS_PER_DAY,
            "remaining_this_hour": MAX_REQUESTS_PER_HOUR,
        }


async def get_usage(user_id: str) -> dict:
    """
    Returns current usage stats for a user without incrementing.
    Used by the frontend to display remaining requests.
    """
    supabase = get_supabase()
    now = _now_utc()
    one_hour_ago = now - timedelta(hours=WINDOW_HOURS)
    one_day_ago  = now - timedelta(days=WINDOW_DAYS)

    try:
        hourly = await run_db(
            supabase.table("rate_limits")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .gte("window_start", one_hour_ago.isoformat())
        )
        daily = await run_db(
            supabase.table("rate_limits")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .gte("window_start", one_day_ago.isoformat())
        )
        hourly_count = hourly.count or 0
        daily_count  = daily.count or 0

        return {
            "requests_this_hour":  hourly_count,
            "requests_today":      daily_count,
            "limit_per_hour":      MAX_REQUESTS_PER_HOUR,
            "limit_per_day":       MAX_REQUESTS_PER_DAY,
            "remaining_this_hour": max(0, MAX_REQUESTS_PER_HOUR - hourly_count),
            "remaining_today":     max(0, MAX_REQUESTS_PER_DAY  - daily_count),
        }

    except Exception as e:
        print(f"[rate_limiter] get_usage error for user {user_id}: {e}")
        return {
            "requests_this_hour":  0,
            "requests_today":      0,
            "limit_per_hour":      MAX_REQUESTS_PER_HOUR,
            "limit_per_day":       MAX_REQUESTS_PER_DAY,
            "remaining_this_hour": MAX_REQUESTS_PER_HOUR,
            "remaining_today":     MAX_REQUESTS_PER_DAY,
        }
