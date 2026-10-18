import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, cast

import sentry_sdk
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from .config.settings import settings
from .db import dbs
from .db.dbs import (
    _init_db,
    close_checkpointer,
    init_checkpointer,
)
from .db.graphdb import close_graph, init_graph
from .db.redisdb import close_redis, init_redis
from .db.vectordb import init_qdrant
from .router import (
    auth_router,
    basic_router,
    message_router,
    rag_router,
    session_router,
)
from .router.limiter import limiter
from .router.setup import close_http_client

# ---------------------------------------------------------
# 1. Configuration & Global Setup
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(BASE_DIR, "../logs"))
os.makedirs(LOG_DIR, exist_ok=True)
SERVER_LOG_FILE = os.path.join(LOG_DIR, "server.log")


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
    _l = logging.getLogger(logger_name)
    _l.handlers = [InterceptHandler()]
    _l.propagate = False

logger.add(
    SERVER_LOG_FILE,
    rotation="10 MB",
    retention="10 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    enqueue=True,
)


sentry_dsn = settings.sentry_dsn
if sentry_dsn:
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
        )
        logger.info("Sentry monitoring initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize Sentry: {e}")
else:
    logger.info("Sentry monitoring disabled (SENTRY_DSN not configured)")


# ---------------------------------------------------------
# 2. Application Lifespan
# ---------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Init engines and create tables — must be first
    _init_db()
    from sqlmodel import SQLModel

    from src.models import models

    assert dbs.engine is not None
    SQLModel.metadata.create_all(dbs.engine)

    # 2. Warm up the async connection pool so the first real request
    #    doesn't pay the TCP handshake + pool-init cost (~500ms)
    try:
        from sqlalchemy import text

        assert dbs.async_engine is not None
        async with dbs.async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Async DB pool warmed up")
    except Exception as e:
        logger.warning(f"DB pool warmup failed (non-fatal): {e}")

    # 3. LangGraph checkpointer
    await init_checkpointer()

    try:
        init_graph()
        logger.info("FalkorDB initialized")
    except Exception as e:
        logger.error(f"Failed to initialize FalkorDB: {e}")

    try:
        init_redis()
        logger.info("Redis initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Redis: {e}")

    # 4. Qdrant collections
    try:
        await init_qdrant()
        logger.info("Qdrant initialized and collections verified")
    except Exception as e:
        logger.error(f"Failed to initialize Qdrant collections: {e}")

    yield

    await close_checkpointer()
    await close_graph()
    await close_redis()
    await close_http_client()


# ---------------------------------------------------------
# 3. App Initialization
# ---------------------------------------------------------
app = FastAPI(
    title="CodeMap API",
    description="Backend services for CodeMap",
    version="1.0.0",
    lifespan=lifespan,
)

logger.info("Server application initialized")

# ---------------------------------------------------------
# 4. Exception Handlers & Rate Limiting
# ---------------------------------------------------------
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    cast(Any, _rate_limit_exceeded_handler),
)

# ---------------------------------------------------------
# 5. Middlewares
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "https://central-gpt.vercel.app",
        "https://central-gpt-frontend.vercel.app",
        "https://main.d2r3zpg0x741h8.amplifyapp.com",
        "https://centralgpt.app",
        "https://www.centralgpt.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    logger.info(f"Incoming request: {request.method} {request.url.path}")
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 2)
    logger.info(
        f"{request.method} {request.url.path} → {response.status_code} ({duration}ms)"
    )
    return response


# ---------------------------------------------------------
# 6. Routers
# ---------------------------------------------------------
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(basic_router, prefix="/setup", tags=["Chat API"])
app.include_router(session_router, prefix="/sessions", tags=["Session"])
app.include_router(message_router, prefix="/messages", tags=["Message"])
app.include_router(rag_router, prefix="/rag", tags=["Rag"])


# ---------------------------------------------------------
# 7. Base Routes
# ---------------------------------------------------------
@app.get("/")
@app.get("/health")
async def root():
    return {"message": "API is running"}


# ---------------------------------------------------------
# 8. Execution
# ---------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(
        "src.main:app", port=port, reload=True, reload_dirs=["src"], log_level="info"
    )
