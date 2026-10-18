from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables before importing modules that rely on them
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db.db_conn import init_db
from routers import (
    app_info,
    auth_ext,
    clubs_router,
    core,
    runs_router,
    territories_router,
    users_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Run to Claim (Base Run) API",
    description="Backend API supporting all 12 screens of the Run to Claim mobile application.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Flutter mobile clients and web interfaces
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Core Base Run routes
app.include_router(core.router)

# Mount feature routers at root level for Flutter ApiConfig compatibility
app.include_router(auth_ext.router)
app.include_router(users_router.router)
app.include_router(app_info.router)
app.include_router(runs_router.router)
app.include_router(territories_router.router)
app.include_router(clubs_router.router)

# Also mount under /api/v1 for standard REST API versioning
app.include_router(auth_ext.router, prefix="/api/v1")
app.include_router(users_router.router, prefix="/api/v1")



@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "message": "Welcome to the Runners Application!"}