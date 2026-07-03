"""Unified entrypoint for all FastAPI microservices."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

BACKEND_ROOT = Path(__file__).resolve().parent

# service name -> (relative path under services/, port)
SERVICES: dict[str, tuple[str, int]] = {
    "api-gateway": ("api-gateway", 8000),
    "velocity-agent": ("velocity-agent", 8001),
    "geo-agent": ("geo-agent", 8002),
    "behavior-agent": ("behavior-agent", 8003),
    "synthesis-agent": ("synthesis-agent", 8004),
    "decision-otp-service": ("decision-otp-service", 8005),
}


def main() -> None:
    service = os.environ.get("SERVICE") or (sys.argv[1] if len(sys.argv) > 1 else "api-gateway")
    if service not in SERVICES:
        known = ", ".join(sorted(SERVICES))
        raise SystemExit(f"Unknown service '{service}'. Choose one of: {known}")

    rel_path, default_port = SERVICES[service]
    service_dir = BACKEND_ROOT / "services" / rel_path
    port = int(os.environ.get("PORT", default_port))
    reload = os.environ.get("APP_ENV", "development") == "development"

    # Ensure shared/ and the service app/ package are importable.
    sys.path[:0] = [str(BACKEND_ROOT), str(service_dir)]

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        reload_dirs=[str(service_dir), str(BACKEND_ROOT / "shared")],
    )


if __name__ == "__main__":
    main()
