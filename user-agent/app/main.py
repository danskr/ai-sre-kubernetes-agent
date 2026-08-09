import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone

import httpx

TARGET_URL = os.getenv(
    "TARGET_URL",
    "http://bulletin-board.bulletin-board.svc.cluster.local/api/v1/messages?limit=1",
)
MIN_INTERVAL_SECONDS = float(os.getenv("MIN_INTERVAL_SECONDS", "3"))
MAX_INTERVAL_SECONDS = float(os.getenv("MAX_INTERVAL_SECONDS", "5"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "2"))

if MIN_INTERVAL_SECONDS <= 0:
    raise ValueError("MIN_INTERVAL_SECONDS must be greater than 0")
if MAX_INTERVAL_SECONDS < MIN_INTERVAL_SECONDS:
    raise ValueError("MAX_INTERVAL_SECONDS must be >= MIN_INTERVAL_SECONDS")
if REQUEST_TIMEOUT_SECONDS <= 0:
    raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than 0")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("user-agent")

running = True


def stop_handler(signum, frame):
    global running
    logger.info("shutdown_requested signal=%s", signum)
    running = False


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    logger.info(
        "user_agent_started target_url=%s interval_seconds=%.1f-%.1f timeout_seconds=%.1f",
        TARGET_URL,
        MIN_INTERVAL_SECONDS,
        MAX_INTERVAL_SECONDS,
        REQUEST_TIMEOUT_SECONDS,
    )

    request_number = 0

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        while running:
            request_number += 1
            started = time.perf_counter()

            try:
                response = client.get(TARGET_URL)
                duration_ms = (time.perf_counter() - started) * 1000
                request_id = response.headers.get("x-request-id", "-")

                logger.info(
                    "request_completed request_number=%d timestamp=%s status=%d duration_ms=%.2f request_id=%s",
                    request_number,
                    utc_now(),
                    response.status_code,
                    duration_ms,
                    request_id,
                )
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                logger.warning(
                    "request_failed request_number=%d timestamp=%s duration_ms=%.2f error_type=%s error=%s",
                    request_number,
                    utc_now(),
                    duration_ms,
                    type(exc).__name__,
                    str(exc),
                )

            if not running:
                break

            delay = random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
            time.sleep(delay)

    logger.info("user_agent_stopped total_requests=%d", request_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
