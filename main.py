import asyncio
import logging

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
from api.routes.buyer import router as buyer_router
from api.routes.workshop import router as workshop_router
from db.connection import close_pool, create_pool, get_pool
from events.producer import get_producer, start_producer, stop_producer
from observability import CorrelationIdFilter
from services.coordinator import build_coordinator
from workers.allocation_worker import AllocationWorker
from workers.auto_verify_worker import AutoVerifyWorker
from workers.notification_worker import NotificationWorker
from workers.verification_worker import VerificationWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [correlation_id=%(correlation_id)s] — %(message)s",
)
logging.getLogger().handlers[0].addFilter(CorrelationIdFilter())
logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI):
    pool = await create_pool()
    await start_producer()

    coordinator = build_coordinator(pool)
    set_coordinator(coordinator)

    allocation_worker = AllocationWorker(coordinator)
    verification_worker = VerificationWorker(coordinator)
    notification_worker = NotificationWorker(coordinator)
    auto_verify_worker = AutoVerifyWorker(coordinator)
    worker_tasks = [
        asyncio.create_task(allocation_worker.run(), name="allocation-worker"),
        asyncio.create_task(verification_worker.run(), name="verification-worker"),
        asyncio.create_task(notification_worker.run(), name="notification-worker"),
        asyncio.create_task(auto_verify_worker.run(), name="auto-verify-worker"),
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
