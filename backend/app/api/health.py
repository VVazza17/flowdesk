from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    # DECISION: liveness only means "the process answers". It deliberately touches no DB,
    # Redis or clock, so killing a dependency can never turn this into a 500. Dependency
    # state belongs to /health/ready, /health/data and /health/providers (slice 6).
    return {"status": "ok"}
