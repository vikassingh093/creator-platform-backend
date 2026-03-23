from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database_setup import create_tables, create_admin
from app.routers import auth, users, creators, wallet, chat
from app.websocket.chat_manager import router as ws_router
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Creator Platform API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(f"{settings.UPLOAD_DIR}/photos", exist_ok=True)
os.makedirs(f"{settings.UPLOAD_DIR}/videos", exist_ok=True)
os.makedirs(f"{settings.UPLOAD_DIR}/profiles", exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(creators.router, prefix="/api/v1")
app.include_router(wallet.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(ws_router)

@app.on_event("startup")
async def startup():
    logger.info("🚀 Starting Creator Hub API...")
    create_tables()
    create_admin()
    logger.info(f"✅ Server ready at http://localhost:{settings.APP_PORT}")

@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}