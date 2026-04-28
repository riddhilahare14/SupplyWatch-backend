# """
# FastAPI application entry point.
# Lifespan: run migrations, seed data, start stream processor.
# Registers all routers and WebSocket endpoint.
# """
# import asyncio
# import logging
# from contextlib import asynccontextmanager
# from fastapi import FastAPI, WebSocket, WebSocketDisconnect
# from fastapi.middleware.cors import CORSMiddleware
# from app.config import settings
# from app.database import engine, Base, async_session
# from app.redis_client import get_redis, close_redis
# from app.services.websocket_manager import ws_manager
# from app.services.stream_processor import gps_ingestion_loop
# from app.routers import shipments, disruptions, predictions, reroutes, internal, metrics
# from app.services.keepalive import start_keepalive

# logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# logger = logging.getLogger(__name__)

# _bg_tasks = []


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     logger.info("Starting %s...", settings.APP_NAME)

#     # Create tables automatically on startup
#     async with engine.begin() as conn:
#         if not settings.USE_SQLITE:
#             from sqlalchemy import text
#             await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
#         await conn.run_sync(Base.metadata.create_all)
#     logger.info("Database tables created/verified")

#     # Initialize Redis
#     await get_redis()

#     # Seed data
#     if settings.SEED_ON_STARTUP:
#         try:
#             from app.seed.seed import seed_database
#             async with async_session() as session:
#                 await seed_database(session)
#         except Exception as e:
#             logger.error("Seed failed: %s", e)

#     # Start background GPS ingestion loop
#     task = asyncio.create_task(gps_ingestion_loop())
#     _bg_tasks.append(task)

#     # ── Keep Render dyno alive ──────────────────────────────────────────
#     _bg_tasks.append(start_keepalive())                     # ← add this
#     # ───────────────────────────────────────────────────────────────────


#     yield

#     # Shutdown
#     for t in _bg_tasks:
#         t.cancel()
#     await close_redis()
#     await engine.dispose()
#     logger.info("Shutdown complete")


# app = FastAPI(
#     title=settings.APP_NAME,
#     description="AI-driven logistics platform — backend API & data layer",
#     version="1.0.0",
#     lifespan=lifespan,
# )

# # CORS — allow all in dev
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # Register routers
# app.include_router(shipments.router)
# app.include_router(disruptions.router)
# app.include_router(predictions.router)
# app.include_router(reroutes.router)
# app.include_router(internal.router)
# app.include_router(metrics.router)


# # WebSocket endpoint
# @app.websocket("/ws/live")
# async def websocket_endpoint(websocket: WebSocket):
#     await ws_manager.connect(websocket)
#     try:
#         while True:
#             # Keep connection alive, receive any client messages
#             data = await websocket.receive_text()
#             # Echo back or handle client commands if needed
#     except WebSocketDisconnect:
#         ws_manager.disconnect(websocket)
#     except Exception:
#         ws_manager.disconnect(websocket)


# @app.get("/")
# async def root():
#     return {
#         "name": settings.APP_NAME,
#         "version": "1.0.0",
#         "docs": "/docs",
#         "health": "/health",
#     }



"""
FastAPI application entry point.
Lifespan: run migrations, seed data, start stream processor.
Registers all routers and WebSocket endpoint.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base, async_session
from app.redis_client import get_redis, close_redis
from app.services.websocket_manager import ws_manager
from app.services.stream_processor import gps_ingestion_loop
from app.services.keepalive import start_keepalive
from app.routers import shipments, disruptions, predictions, reroutes, internal, metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_bg_tasks = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s...", settings.APP_NAME)

    # Create tables automatically on startup
    async with engine.begin() as conn:
        if not settings.USE_SQLITE:
            from sqlalchemy import text
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    # Initialize Redis
    await get_redis()

    # Seed data
    if settings.SEED_ON_STARTUP:
        try:
            from app.seed.seed import seed_database
            async with async_session() as session:
                await seed_database(session)
        except Exception as e:
            logger.error("Seed failed: %s", e)

    # Start background GPS ingestion loop
    task = asyncio.create_task(gps_ingestion_loop())
    _bg_tasks.append(task)

    # Keep Render free-tier dyno alive
    if settings.KEEPALIVE_ENABLED:
        _bg_tasks.append(start_keepalive())

    yield

    # Shutdown
    for t in _bg_tasks:
        t.cancel()
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-driven logistics platform — backend API & data layer",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — driven by ALLOWED_ORIGINS env var ("*" for dev, explicit origins for prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(shipments.router)
app.include_router(disruptions.router)
app.include_router(predictions.router)
app.include_router(reroutes.router)
app.include_router(internal.router)
app.include_router(metrics.router)


# WebSocket endpoint
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.get("/")
async def root():
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }