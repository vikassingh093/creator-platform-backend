from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import APP_NAME, DEBUG, APP_ENV
from app.routers import auth, users, creators, chat, wallet, content, admin, notifications, calls, admin_offers, offers
from app.database import get_pool_status, execute_query
from app.middleware.activity_tracker import ActivityTrackerMiddleware
import os
import logging

logger = logging.getLogger("app.main")

app = FastAPI(
    title=APP_NAME,
    docs_url="/docs" if DEBUG else None,       # ✅ Disable Swagger in production
    redoc_url="/redoc" if DEBUG else None,      # ✅ Disable ReDoc in production  
    openapi_url="/openapi.json" if DEBUG else None,  # ✅ Disable OpenAPI schema in production
)

# ✅ Activity tracker FIRST
app.add_middleware(ActivityTrackerMiddleware)

# ✅ CORS — restrict in production
if DEBUG:
    # Local: allow everything
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Production: only your domains
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",")
    # Filter out empty strings
    ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]
    if not ALLOWED_ORIGINS:
        ALLOWED_ORIGINS = ["*"]  # Fallback if not set in .env
        logger.warning("⚠️ ALLOWED_ORIGINS not set in .env — using wildcard (not recommended)")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

# Upload directory
os.makedirs("uploads/profile_photos", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ✅ Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(creators.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(wallet.router, prefix="/api/v1")
app.include_router(content.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(calls.router, prefix="/api/v1")
app.include_router(admin_offers.router, prefix="/api/v1")
app.include_router(offers.router, prefix="/api/v1")

# ✅ Health check — monitor DB pool in production
@app.get("/health")
def health_check():
    try:
        result = execute_query("SELECT 1 as ok", fetch_one=True)
        db_ok = result and result.get("ok") == 1
    except Exception:
        db_ok = False

    pool = get_pool_status()
    return {
        "status": "healthy" if db_ok else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
        "environment": APP_ENV,
        "debug": DEBUG,
        "pool": pool,
    }

# ✅ Startup event
@app.on_event("startup")
async def startup_event():
    logger.info(f"🚀 {APP_NAME} server started | ENV={APP_ENV} | DEBUG={DEBUG}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info(f"🛑 {APP_NAME} server shutting down")