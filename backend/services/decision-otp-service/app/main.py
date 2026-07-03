from fastapi import FastAPI

from app.routers.evaluate import router as evaluate_router
from shared.constants.service_names import DECISION_OTP_SERVICE
from shared.routers.health import health_router

app = FastAPI(
    title="Decision & OTP Service",
    version="0.1.0",
    description="PASS/OTP/BLOCK threshold logic and dual-path OTP interlock.",
)

app.include_router(health_router(DECISION_OTP_SERVICE))
app.include_router(evaluate_router)
