"""
Simple in-memory rate limiter by IP.
For production, swap to Redis-backed.
"""

import time
from collections import defaultdict
from functools import wraps

from flask import request, jsonify

# Storage: {ip: [(timestamp, tool_name), ...]}
_requests = defaultdict(list)

# Defaults
DEFAULT_LIMIT = 3       # requests per window
DEFAULT_WINDOW = 86400  # 24 hours


def _clean(ip, window):
    cutoff = time.time() - window
    _requests[ip] = [(ts, tool) for ts, tool in _requests[ip] if ts > cutoff]


def check_rate_limit(tool_name: str = "general",
                     limit: int = DEFAULT_LIMIT,
                     window: int = DEFAULT_WINDOW) -> tuple[bool, int]:
    """Check if the current request is within limits.
    Returns (allowed, remaining)."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    _clean(ip, window)

    # Count requests for this specific tool
    tool_requests = [(ts, t) for ts, t in _requests[ip] if t == tool_name]

    if len(tool_requests) >= limit:
        return False, 0

    return True, limit - len(tool_requests)


def record_request(tool_name: str = "general"):
    """Record a request after it's been served."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    _requests[ip].append((time.time(), tool_name))


def rate_limit(tool_name: str = "general",
               limit: int = DEFAULT_LIMIT,
               window: int = DEFAULT_WINDOW):
    """Decorator for Flask routes."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            allowed, remaining = check_rate_limit(tool_name, limit, window)
            if not allowed:
                return jsonify({
                    "error": "Rate limit exceeded. Free tier allows "
                             f"{limit} requests per day. Try again tomorrow.",
                    "limit": limit,
                    "remaining": 0,
                }), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator
