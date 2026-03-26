from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import APP_NAME
from app.routers import auth, users, creators, chat, wallet, content, admin, notifications, calls
import os

app = FastAPI(title=APP_NAME)

# ✅ Serve uploaded files as static
os.makedirs("uploads/images", exist_ok=True)
os.makedirs("uploads/videos", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")