import json
import os
import socket
import uuid

import grpc
import pika
import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import catalogo_pb2
import catalogo_pb2_grpc
from logging_setup import correlation_id_var, setup

SERVICE_NAME = "pedidos"
CATALOGO_ADDR = os.getenv("CATALOGO_ADDR", "catalogo:50051")
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/")
GRPC_TIMEOUT = float(os.getenv("GRPC_TIMEOUT", "0.3"))
INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://inventario:8001")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "1.0"))

log = setup(SERVICE_NAME)
app = FastAPI(title="Pedidos MPI")


class OrderRequest(BaseModel):
    sku: str
    cantidad: int


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    correlation_id_var.set(cid)
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response


def _consultar_stock(sku: str, cid: str):
    """Llamada gRPC sincrona con timeout y correlation_id en metadata."""
    with grpc.insecure_channel(CATALOGO_ADDR) as channel:
        stub = catalogo_pb2_grpc.CatalogoStub(channel)
        metadata = (("x-correlation-id", cid),)
        return stub.ConsultarStock(
            catalogo_pb2.StockRequest(sku=sku),
            timeout=GRPC_TIMEOUT,
            metadata=metadata,
        )


def _reservar_stock(sku: str, cantidad: int, cid: str):
    """Reserva stock en el servicio de inventario usando Redis lock."""
    try:
        response = requests.post(
            f"{INVENTARIO_URL}/reserve",
            json={
                "sku": sku,
                "cantidad": cantidad,
            },
            headers={
                "X-Correlation-Id": cid,
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        log.warning(f"inventario no disponible: {e}")
        raise HTTPException(status_code=503, detail="inventario no disponible")

    if response.status_code == 400:
        raise HTTPException(status_code=400, detail="sin stock")

    if response.status_code == 503:
        raise HTTPException(status_code=503, detail="inventario ocupado, reintente")

    if response.status_code >= 400:
        log.warning(f"inventario error {response.status_code}: {response.text}")
        raise HTTPException(status_code=502, detail="error en inventario")

    return response.json()


def _publicar_email(order_id: str, sku: str, cid: str):
    """Publica a RabbitMQ con cola durable + delivery_mode=2 persistente."""
    conn = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
    try:
        ch = conn.channel()
        ch.confirm_delivery()
        ch.queue_declare(queue="emails", durable=True)
        ch.basic_publish(
            exchange="",
            routing_key="emails",
            body=json.dumps({"order_id": order_id, "sku": sku}),
            properties=pika.BasicProperties(
                delivery_mode=2,
                headers={"x-correlation-id": cid},
                content_type="application/json",
            ),
        )
    finally:
        conn.close()


@app.post("/orders", status_code=201)
def crear_pedido(req: OrderRequest):
    cid = correlation_id_var.get()
    log.info(f"crear_pedido sku={req.sku} cantidad={req.cantidad}")

    # 1) Consulta catálogo por gRPC para validar existencia/precio.
    try:
        stock_catalogo = _consultar_stock(req.sku, cid)
    except grpc.RpcError as e:
        log.warning(f"grpc error: {e.code()} {e.details()}")
        raise HTTPException(status_code=503, detail="catalogo no disponible")

    if not stock_catalogo.disponible:
        raise HTTPException(status_code=400, detail="producto no disponible")

    # 2) Reserva real de stock en inventario.
    # Esta es la parte crítica del TP3: evita overselling con Redis lock.
    reserva = _reservar_stock(req.sku, req.cantidad, cid)

    # 3) Crear orden.
    order_id = f"ORD-{uuid.uuid4().hex[:8]}"

    # 4) Publicar evento asincrónico.
    try:
        _publicar_email(order_id, req.sku, cid)
    except Exception as e:
        # Limitación conocida: si falla RabbitMQ después de reservar stock,
        # queda stock reservado pero sin email. En producción se usaría Outbox/Saga.
        log.error(f"publish fallo: {e}")
        raise HTTPException(status_code=502, detail="broker no disponible")

    log.info(f"order_id={order_id} creado precio={stock_catalogo.precio}")

    return {
        "order_id": order_id,
        "sku": req.sku,
        "cantidad": req.cantidad,
        "precio": stock_catalogo.precio,
        "stock_remaining": reserva["stock_remaining"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    problemas = []

    # Catalogo accesible?
    try:
        host, port = CATALOGO_ADDR.split(":")
        with socket.create_connection((host, int(port)), timeout=0.5):
            pass
    except Exception as e:
        problemas.append(f"catalogo: {e}")

    # RabbitMQ accesible?
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
        conn.close()
    except Exception as e:
        problemas.append(f"rabbit: {e}")

    # Inventario accesible?
    try:
        response = requests.get(f"{INVENTARIO_URL}/ready", timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            problemas.append(f"inventario: status {response.status_code}")
    except Exception as e:
        problemas.append(f"inventario: {e}")

    if problemas:
        raise HTTPException(status_code=503, detail=problemas)

    return {"status": "ready"}