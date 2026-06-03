import os
import time
from contextlib import contextmanager

import redis
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
LOCK_TTL_SECONDS = int(os.getenv("LOCK_TTL_SECONDS", "5"))

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)

app = FastAPI(title="Inventario MPI")


# =========================
# Métricas Prometheus
# =========================

reserve_attempts_total = Counter(
    "reserve_attempts_total",
    "Cantidad total de intentos de reserva",
    ["result"],
)

reserve_duration_seconds = Histogram(
    "reserve_duration_seconds",
    "Duracion de cada intento de reserva en segundos",
)

inventory_stock_level = Gauge(
    "inventory_stock_level",
    "Stock actual por SKU",
    ["sku"],
)

overselling_attempts_total = Counter(
    "overselling_attempts_total",
    "Intentos detectados de vender mas stock del disponible",
)


# =========================
# Schemas
# =========================

class ReserveRequest(BaseModel):
    sku: str = Field(..., min_length=1)
    cantidad: int = Field(..., gt=0)


class StockRequest(BaseModel):
    sku: str = Field(..., min_length=1)
    cantidad: int = Field(..., ge=0)


# =========================
# Helpers Redis
# =========================

def stock_key(sku: str) -> str:
    return f"stock:{sku}"


def lock_key(sku: str) -> str:
    return f"lock:{sku}"


def get_stock(sku: str) -> int:
    value = r.get(stock_key(sku))
    return int(value) if value is not None else 0


def set_stock(sku: str, cantidad: int) -> None:
    r.set(stock_key(sku), cantidad)
    inventory_stock_level.labels(sku=sku).set(cantidad)


@contextmanager
def redis_lock(sku: str):
    key = lock_key(sku)

    lock_obtenido = r.set(
        key,
        "reserved",
        nx=True,
        ex=LOCK_TTL_SECONDS,
    )

    if not lock_obtenido:
        raise HTTPException(
            status_code=503,
            detail="Otro usuario esta reservando este producto. Reintente.",
        )

    try:
        yield
    finally:
        r.delete(key)


# =========================
# Endpoints
# =========================

@app.post("/stock")
def cargar_stock(req: StockRequest):
    try:
        set_stock(req.sku, req.cantidad)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Redis no disponible")

    return {
        "sku": req.sku,
        "stock": req.cantidad,
    }


@app.get("/stock/{sku}")
def consultar_stock(sku: str):
    try:
        stock = get_stock(sku)
        inventory_stock_level.labels(sku=sku).set(stock)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Redis no disponible")

    return {
        "sku": sku,
        "stock": stock,
    }


@app.post("/reserve")
def reservar(req: ReserveRequest):
    start = time.time()

    try:
        with redis_lock(req.sku):
            stock_actual = get_stock(req.sku)

            if stock_actual < req.cantidad:
                overselling_attempts_total.inc()
                reserve_attempts_total.labels(result="sin_stock").inc()

                raise HTTPException(
                    status_code=400,
                    detail="Sin stock suficiente",
                )

            stock_nuevo = stock_actual - req.cantidad
            set_stock(req.sku, stock_nuevo)

            reserve_attempts_total.labels(result="ok").inc()

            return {
                "status": "reserved",
                "sku": req.sku,
                "cantidad": req.cantidad,
                "stock_remaining": stock_nuevo,
            }

    except HTTPException as e:
        if e.status_code == 503:
            reserve_attempts_total.labels(result="lock_ocupado").inc()
        raise e

    except redis.RedisError:
        reserve_attempts_total.labels(result="redis_error").inc()
        raise HTTPException(status_code=503, detail="Redis no disponible")

    finally:
        reserve_duration_seconds.observe(time.time() - start)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        r.ping()
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Redis no disponible")

    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )