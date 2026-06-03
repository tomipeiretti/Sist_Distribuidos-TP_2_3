#!/usr/bin/env bash
# Genera los stubs de Python a partir de catalogo.proto en ambos servicios.
# Solo necesario si se quiere correr fuera de Docker. Los Dockerfiles ya
# regeneran al buildear.

set -euo pipefail
cd "$(dirname "$0")/.."

for svc in catalogo pedidos; do
  echo ">> generando stubs en $svc/"
  (cd "$svc" && python -m grpc_tools.protoc \
    --python_out=. \
    --grpc_python_out=. \
    -I. catalogo.proto)
done

echo "OK. Archivos generados: catalogo_pb2.py, catalogo_pb2_grpc.py en cada servicio."
