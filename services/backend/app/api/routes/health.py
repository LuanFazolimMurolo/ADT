from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    """Get API health status."""
    return {"status": "healthy"}


@router.get("/readiness")
async def readiness() -> dict[str, str]:
    """Get API readiness status."""
    return {"status": "ready"}
