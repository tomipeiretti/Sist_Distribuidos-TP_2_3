#!/usr/bin/env bash
# Demo end-to-end del sistema. Asume que ya esta corriendo (compose o k8s).
# Uso:
#   ./scripts/demo.sh           # contra docker-compose (localhost:8000)
#   PEDIDOS_URL=http://localhost:8080 ./scripts/demo.sh

set -euo pipefail

PEDIDOS_URL="${PEDIDOS_URL:-http://localhost:8000}"

echo "=== 1. Camino feliz: crear pedido con stock ==="
curl -sS -X POST "$PEDIDOS_URL/orders" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-Id: demo-$(date +%s)" \
  -d '{"sku":"SKU-001","cantidad":2}' | tee /tmp/demo_resp.json
echo
echo

echo "=== 2. Sin stock: SKU agotado ==="
curl -sS -X POST "$PEDIDOS_URL/orders" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-003","cantidad":1}' -w "\nHTTP %{http_code}\n"
echo

echo "=== 3. SKU inexistente ==="
curl -sS -X POST "$PEDIDOS_URL/orders" \
  -H "Content-Type: application/json" \
  -d '{"sku":"NOPE","cantidad":1}' -w "\nHTTP %{http_code}\n"
echo

echo "=== 4. Health/Ready ==="
curl -sS "$PEDIDOS_URL/health" && echo
curl -sS "$PEDIDOS_URL/ready"  && echo
echo

echo "Listo. Revisa:"
echo "  - logs de notificaciones: docker logs mpi-notificaciones -f"
echo "  -                      o: kubectl logs -l app=notificaciones -f"
echo "  - UI RabbitMQ: http://localhost:15672 (guest/guest)"
