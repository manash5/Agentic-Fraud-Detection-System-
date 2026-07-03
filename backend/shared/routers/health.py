"""Reusable health-check router for all services."""

from fastapi import APIRouter


def health_router(service_name: str) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    def health_check() -> dict[str, str]:
        return {"service": service_name, "status": "ok"}

    return router
