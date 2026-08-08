"""
FastAPI application factory.

Architecture decisions enforced here:
- Household ID NEVER accepted as a client parameter — always from JWT.
- Idempotency keys stored in Redis; duplicate requests get the cached response.
- All routers versioned under /v1.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import approvals, auth, definitions, household, reports, series, tasks, wallet

app = FastAPI(
    title="KidsChores API",
    description="Family task & rewards app for teens (13-17).",
    version="0.1.0",
    docs_url="/docs" if settings.app_debug else None,
    redoc_url="/redoc" if settings.app_debug else None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(household.router)
app.include_router(tasks.router)
app.include_router(definitions.router)
app.include_router(series.router)
app.include_router(wallet.router)
app.include_router(approvals.router)
app.include_router(reports.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
