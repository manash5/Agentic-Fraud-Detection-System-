from fastapi import FastAPI

from app.routers.evaluate import router as evaluate_router
from shared.constants.service_names import SYNTHESIS_AGENT
from shared.routers.health import health_router

app = FastAPI(
    title="Synthesis Agent",
    version="0.1.0",
    description="Two-layer dynamic weight blending and confidence-weighted score synthesis.",
)

app.include_router(health_router(SYNTHESIS_AGENT))
app.include_router(evaluate_router)
