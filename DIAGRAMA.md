# Diagrama de arquitectura — MPI Microservicios

## Vista lógica (flujos de comunicación)

```
                          ┌───────────────────────┐
                          │       Cliente         │
                          │  (browser, curl, app) │
                          └───────────┬───────────┘
                                      │
                                      │   REST  POST /orders
                                      │   (HTTP/1.1, JSON, X-Correlation-Id)
                                      ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║                       Cluster Kubernetes                          ║
   ║                                                                   ║
   ║   ┌──────────────────┐                                            ║
   ║   │  Svc: pedidos    │  ← DNS estable, balancea entre replicas    ║
   ║   └────────┬─────────┘                                            ║
   ║            │                                                      ║
   ║   ┌────────▼─────────┐    gRPC sync (HTTP/2, Protobuf)            ║
   ║   │   Pod: pedidos   │  ─────────────────────────────────┐        ║
   ║   │  (replicas=2)    │   ConsultarStock(sku)              │       ║
   ║   │   FastAPI        │   timeout=300ms, deadline propag.  │       ║
   ║   │                  │                                    ▼       ║
   ║   │  /health  /ready │                          ┌──────────────┐  ║
   ║   └────────┬─────────┘                          │ Svc:catalogo │  ║
   ║            │                                    └──────┬───────┘  ║
   ║            │  AMQP publish                             │          ║
   ║            │  durable + delivery_mode=2                │          ║
   ║            │  + publisher confirms                     ▼          ║
   ║            │  + headers[x-correlation-id]      ┌──────────────┐   ║
   ║            ▼                                   │Pod:catalogo  │   ║
   ║   ┌──────────────────┐                         │  (replicas=2)│   ║
   ║   │  Svc: rabbitmq   │ ★ SPOF                  │  gRPC server │   ║
   ║   │     :5672 amqp   │   (durable, persistent  │  reflection  │   ║
   ║   │     :15672 mgmt  │    mitiga, no elimina)  └──────────────┘   ║
   ║   └────────┬─────────┘                                            ║
   ║            │                                                      ║
   ║            │  consume(prefetch=1, ack manual)                     ║
   ║            ▼                                                      ║
   ║   ┌──────────────────┐                                            ║
   ║   │ Pod:notificacion │  consumer idempotente                      ║
   ║   │ (replicas=1, sin │  cache `procesados` → no dup emails        ║
   ║   │  Service)        │                                            ║
   ║   └──────────────────┘                                            ║
   ║                                                                   ║
   ║         ★ SPOF: kube-apiserver / etcd (control plane)             ║
   ║         ★ SPOF: kube-dns / CNI (red interna)                      ║
   ╚══════════════════════════════════════════════════════════════════╝
```

## Vista temporal — flujo "crear pedido"

```
Cliente   Pedidos   Catalogo   RabbitMQ   Notificaciones
  │         │         │           │             │
  │ POST    │         │           │             │
  ├────────►│         │           │             │
  │         │ gRPC    │           │             │
  │         ├────────►│           │             │       ── SYNC
  │         │ stock OK│           │             │
  │         │◄────────┤           │             │
  │         │ publish │           │             │
  │         ├──────────────────► │             │       ── ASYNC
  │         │ confirm │           │             │
  │         │◄────────────────────┤             │
  │ 201 OK  │         │           │             │
  │◄────────┤         │           │             │
  │         │         │           │   deliver   │
  │         │         │           ├────────────►│
  │         │         │           │             │ idempotency check
  │         │         │           │             │ enviar email (mock)
  │         │         │           │   ack       │
  │         │         │           │◄────────────┤
```

## Vista de capas (qué da Docker, qué da K8s)

```
┌────────────────────────────────────────────────────────────────────┐
│  Servicios                                                          │
│  - pedidos:8000 (FastAPI)                                           │
│  - catalogo:50051 (gRPC server)                                     │
│  - notificaciones (pika consumer)                                   │
│  - rabbitmq:5672 / :15672                                           │
└────────────────────────────────────────────────────────────────────┘
              ▲                                       ▲
              │ DNS (Service)                         │ resource limits
              │ replicas / probes                     │ healthchecks
              │ rolling update                        │
┌─────────────┴────────────┐         ┌────────────────┴──────────────┐
│   Kubernetes                       │   Docker                       │
│   - Deployment + Service           │   - imagen multicapa cacheable │
│   - ConfigMap                      │   - non-root user              │
│   - liveness ≠ readiness probes    │   - HEALTHCHECK                │
│   - autohealing                    │   - tag versionado (no :latest)│
└────────────────────────────────────┴────────────────────────────────┘
```

## Convenciones del diagrama

- `★ SPOF` = Single Point of Failure introducido por la arquitectura
  distribuida.
- Flechas continuas = sincrónico (bloqueante).
- Flechas punteadas (en la vista temporal: `►` con cortes) =
  asincrónico (no bloqueante en el origen).
- Recuadros con `Svc:` = objeto `Service` de K8s (DNS + balanceo).
- `Pod:` = pod individual.
