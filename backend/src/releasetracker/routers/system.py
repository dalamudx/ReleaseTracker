from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health/live", include_in_schema=False)
async def live():
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def ready(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        db = await storage._get_connection()
        row = await (await db.execute("SELECT 1")).fetchone()
        if row and row[0] == 1:
            return {"status": "ok"}
    except Exception:
        pass  # Do not reveal storage errors to an unauthenticated caller.
    return JSONResponse({"status": "unavailable"}, status_code=503)
