# MPI Microservicios — TP2 Sistemas Distribuidos

Implementación de **3 microservicios** del caso Market-Place-Inc (MPI) que
demuestran las 4 piezas técnicas de la Unidad II: **Docker, Kubernetes, gRPC,
RabbitMQ**, más diagnóstico end-to-end.

```
Frontend → [Pedidos]  ──gRPC sync──→ [Catalogo]
              │
              └──RabbitMQ async─→ [Notificaciones]
```

## Servicios

| Servicio | Tecnología | Rol |
|---|---|---|
| `catalogo` | gRPC (`grpcio`) | Server. Expone `ConsultarStock(sku)`. |
| `pedidos` | FastAPI + gRPC client + pika | REST público + orquestador. |
| `notificaciones` | pika consumer | Consume cola `emails`. Idempotente. |

## Levantar el sistema

### Docker Compose (recomendado para probar local)
```bash
docker compose up --build
# UI Rabbit:        http://localhost:15672 (guest/guest)
# API pedidos:      http://localhost:8000
# gRPC catalogo:    localhost:50051
```

### Kubernetes (kind / minikube / Docker Desktop)
```bash
# 1) buildear las imagenes en el daemon local
docker build -t mpi/catalogo:v1       ./catalogo
docker build -t mpi/pedidos:v1        ./pedidos
docker build -t mpi/notificaciones:v1 ./notificaciones

# 1b) si usas kind, cargar las imagenes al cluster:
#     kind load docker-image mpi/catalogo:v1 mpi/pedidos:v1 mpi/notificaciones:v1

# 2) desplegar
kubectl apply -f k8s/
kubectl get pods -w

# 3) port-forward para acceso local
kubectl port-forward svc/pedidos  8000:8000  &
kubectl port-forward svc/rabbitmq 15672:15672 &
```

## Probar

```bash
./scripts/demo.sh
```

O manualmente:
```bash
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-001","cantidad":2}'
# 201 {"order_id":"ORD-...", "sku":"SKU-001", "precio":15990.0}
```

## Tabla sync vs async (flujo por flujo)

| Flujo | Protocolo | Por qué | Qué sacrifica |
|---|---|---|---|
| Frontend → Pedidos | REST | Cliente heterogéneo (browser/app), JSON legible | Más overhead que gRPC |
| Pedidos → Catálogo | **gRPC sync** | Necesitamos stock *ahora* o el pedido se rechaza | Si catálogo cae, no se crean pedidos |
| Pedidos → Notificaciones | **RabbitMQ async** | Email puede tardar minutos sin impacto | No sabemos exacto cuándo sale el email |
| Pedidos → Pagos (no implementado, se justifica) | **RabbitMQ async + correlation_id** | Proveedor externo impredecible | Estado del pedido pasa por `PENDING` |

Justificación completa en [`INFORME.md`](./INFORME.md).

## Diagnóstico (Estación 5)

```bash
./scripts/diagnostico.sh    # imprime cheat-sheet kubectl
```

## Otros documentos

- [`INFORME.md`](./INFORME.md) — caso, decisiones, SPOFs, propuestas futuras.
- [`IA_LOG.md`](./IA_LOG.md) — prompts a IA, errores detectados, correcciones.
- [`DIAGRAMA.md`](./DIAGRAMA.md) — diagrama ASCII de arquitectura.

## Checklist de rúbrica (E1–E5)

- [x] Dockerfile no-root + HEALTHCHECK + versión fija (no `:latest`)
- [x] Deployment con `resources.limits` + `livenessProbe` ≠ `readinessProbe`
- [x] gRPC con `timeout` explícito (`0.3s`) + reflection habilitada
- [x] Cola `durable=True`, `delivery_mode=2`, ack manual, idempotencia
- [x] Service discovery por nombre DNS (sin IPs hardcodeadas)
- [x] Propagación de `correlation_id` HTTP → gRPC metadata → AMQP headers
