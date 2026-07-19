import asyncio
import logging
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import set_coordinator
from api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.buyer import router as buyer_router
from api.routes.workshop import router as workshop_router
from db.checkpointer import close_checkpointer, create_checkpointer
from db.connection import close_pool, create_pool, get_pool
from events.producer import get_producer, start_producer, stop_producer
from observability import CorrelationIdFilter
from services.coordinator import build_coordinator
from workers.allocation_worker import AllocationWorker
from workers.auto_verify_worker import AutoVerifyWorker
from workers.notification_worker import NotificationWorker
from workers.stuck_order_worker import StuckOrderWorker
from workers.verification_worker import VerificationWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [correlation_id=%(correlation_id)s] — %(message)s",
)
logging.getLogger().handlers[0].addFilter(CorrelationIdFilter())
logger = logging.getLogger(__name__)


async def _supervise(name: str, worker) -> None:
    """Keep a background worker's run() loop alive across crashes.

    Each worker's own run() already retries recoverable per-message/per-sweep
    failures internally — this is the outer safety net for the case that
    matters more: run() itself exiting (an unhandled exception escaping it,
    or it returning at all), which previously left that worker silently dead
    for the rest of the process's life with nothing else noticing. Kafka
    consumer teardown/rebuild on restart is safe — a fresh AIOKafkaConsumer
    rejoins the same group and resumes from the last committed offset.
    """
    while True:
        try:
            await worker.run()
            logger.critical("%s.run() returned without cancellation — restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.critical("%s crashed — restarting in 5s", name, exc_info=True)
        await asyncio.sleep(5)


async def lifespan(app: FastAPI):
    pool = await create_pool()
    checkpointer = await create_checkpointer()
    await start_producer()

    coordinator = build_coordinator(pool, checkpointer)
    set_coordinator(coordinator)

    allocation_worker = AllocationWorker(coordinator)
    verification_worker = VerificationWorker(coordinator)
    notification_worker = NotificationWorker(coordinator)
    auto_verify_worker = AutoVerifyWorker(coordinator)
    stuck_order_worker = StuckOrderWorker(coordinator)
    worker_tasks = [
        asyncio.create_task(_supervise("AllocationWorker", allocation_worker), name="allocation-worker"),
        asyncio.create_task(_supervise("VerificationWorker", verification_worker), name="verification-worker"),
        asyncio.create_task(_supervise("NotificationWorker", notification_worker), name="notification-worker"),
        asyncio.create_task(_supervise("AutoVerifyWorker", auto_verify_worker), name="auto-verify-worker"),
        asyncio.create_task(_supervise("StuckOrderWorker", stuck_order_worker), name="stuck-order-worker"),
    ]
    logger.info("Saathi API started")

    yield
    for task in worker_tasks:
        task.cancel()
    for task in worker_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass

    await stop_producer()
    await close_checkpointer()
    await close_pool()
    logger.info("Saathi API stopped")


app = FastAPI(
    title="Saathi",
    description="SFURTI consortium coordination platform — agentic order allocation",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(auth_router)
app.include_router(buyer_router)
app.include_router(workshop_router)
app.include_router(admin_router)


@app.get("/health")
async def health() -> JSONResponse:
    db_status = "ok"
    try:
        await get_pool().fetchval("SELECT 1")
    except Exception as exc:
        logger.error("Health check: DB unavailable: %s", exc)
        db_status = "error"

    kafka_status = "ok"
    try:
        get_producer()
    except Exception as exc:
        logger.error("Health check: Kafka producer unavailable: %s", exc)
        kafka_status = "error"

    overall = "ok" if db_status == "ok" and kafka_status == "ok" else "degraded"
    return JSONResponse(
        status_code=200 if overall == "ok" else 503,
        content={"status": overall, "db": db_status, "kafka": kafka_status},
    )
