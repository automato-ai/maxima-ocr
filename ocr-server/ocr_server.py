from fastapi import FastAPI
from pydantic import BaseModel
from contextlib import asynccontextmanager

import random

import usb_cams
import config

app = FastAPI()

# Global variable to track readiness status
app_ready = False

class OCRResponse(BaseModel):
    ocr_id: str
    confidence: float


@app.get("/v1", response_model=OCRResponse)
async def ocr():
    return OCRResponse(
        ocr_id=str(random.randint(1000000, 999999999)),
        confidence=round(random.random(), 2)
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_ready
    # --- Startup logic (before yield) ---
    print("Connecting to USB cameras ...")
    # Perform resource initialization, e.g., connect to databases, load models
    # Simulate a long startup process
    usb_cams.get_cams(config.config)
    app_ready = True
    print("Application startup complete. Ready to serve requests.")
    yield

    # --- Shutdown logic (after yield) ---
    print("Application shutting down...")
    # Clean up resources, e.g., close database connections
