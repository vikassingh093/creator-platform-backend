import logging
import uvicorn
from app.config import APP_PORT, WORKERS

# ✅ Configure logging BEFORE uvicorn starts
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    force=True
)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=APP_PORT,
        reload=True,
        workers=WORKERS,
        log_level="info"   # ← also tell uvicorn to show INFO level
    )