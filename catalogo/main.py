import json
import logging
import os
import sys
from concurrent import futures
from datetime import datetime

import grpc
from grpc_reflection.v1alpha import reflection

import catalogo_pb2
import catalogo_pb2_grpc


SERVICE_NAME = "catalogo"
PORT = int(os.getenv("PORT", "50051"))


class JSONFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": SERVICE_NAME,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
log = logging.getLogger(SERVICE_NAME)
log.addHandler(handler)
log.setLevel(logging.INFO)


# DB simulada. En produccion: Postgres/MySQL exclusivo del servicio.
PRODUCTOS = {
    "SKU-001": {"stock": 50, "precio": 15990.0},
    "SKU-002": {"stock": 3,  "precio": 2499.0},
    "SKU-003": {"stock": 0,  "precio": 8990.0},
    "SKU-042": {"stock": 25, "precio": 4990.0},
}


def _extract_correlation_id(context):
    for k, v in context.invocation_metadata():
        if k.lower() == "x-correlation-id":
            return v
    return "-"


class CatalogoServicer(catalogo_pb2_grpc.CatalogoServicer):
    def ConsultarStock(self, request, context):
        cid = _extract_correlation_id(context)
        log.info(
            f"ConsultarStock sku={request.sku}",
            extra={"correlation_id": cid},
        )
        p = PRODUCTOS.get(request.sku)
        if not p:
            return catalogo_pb2.StockResponse(sku=request.sku, disponible=False)
        return catalogo_pb2.StockResponse(
            sku=request.sku,
            stock=p["stock"],
            precio=p["precio"],
            disponible=p["stock"] > 0,
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    catalogo_pb2_grpc.add_CatalogoServicer_to_server(CatalogoServicer(), server)

    # Reflection: permite a grpcurl listar y llamar metodos sin tener el .proto.
    service_names = (
        catalogo_pb2.DESCRIPTOR.services_by_name["Catalogo"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    log.info(f"catalogo gRPC listo en :{PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
