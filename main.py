import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Load environment variables before importing modules that rely on them
load_dotenv()

import asyncpg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from db.config import db_config
from db.database import check_db_health, close_db, init_db
from routers import (
    app_info,
    auth_ext,
    clubs_router,
    core,
    friends_router,
    runs_router,
    territories_router,
    users_router,
)

logger = logging.getLogger("baserun")

ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    503: "SERVICE_UNAVAILABLE",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the PostgreSQL pool and apply the schema. On serverless cold
    # starts where lifespan does not run, the first query does this lazily.
    try:
        await init_db()
    except Exception as exc:
        logger.error("Database initialisation failed at startup: %s", exc)
    yield
    await close_db()


app = FastAPI(
    title="BASER (Run to Claim) API",
    description="Backend API supporting all 12 screens of the BASER mobile application.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Flutter mobile clients and web interfaces (token auth, no cookies)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail.value if hasattr(exc.detail, "value") else exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail, "code": ERROR_CODES.get(exc.status_code, "ERROR")},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "code": ERROR_CODES[422]},
    )


DB_UNAVAILABLE_ERRORS = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, DB_UNAVAILABLE_ERRORS):
        logger.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable, please retry", "code": ERROR_CODES[503]},
        )
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error", "code": "INTERNAL_ERROR"})


# Mount Core Base Run routes
app.include_router(core.router)

# Before the /api/v1/users mount so /api/v1/users/search is matched here
app.include_router(friends_router.router)

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
app.include_router(runs_router.router, prefix="/api/v1")


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "message": "Welcome to the Runners Application!"}


@app.get("/health/db", tags=["health"])
async def db_health():
    """
    PostgreSQL connectivity, latency and active session count.
    Returns 503 when the database is unreachable.
    """
    health = await check_db_health()
    health["config"] = db_config.to_dict(mask_secrets=True)
    status = 200 if health["connection"] == "ok" else 503
    return JSONResponse(status_code=status, content=health)
