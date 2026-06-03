# Informe TP2 — Sistemas Distribuidos · Ciclo 2026

**Caso:** Market-Place-Inc (MPI), parte 2 — descomposición del monolito.
**Equipo:** *(completar)*
**Fecha de entrega:** *(completar)*

---

## 1. Resumen ejecutivo

Después del TP1, MPI separó el monolito en cinco servicios FastAPI
independientes (catálogo, pedidos, inventario, pagos, notificaciones), cada
uno con su propia base de datos. La separación resolvió los locks de
inventario que bloqueaban el catálogo, pero **introdujo tres problemas
nuevos típicos de sistemas distribuidos**:

1. **Fallas en cascada** — un servicio lento bloqueaba toda la cadena de
   requests sincrónicos.
2. **Dev setup imposible** — onboarding de 2 días por la cantidad de
   procesos a levantar a mano.
3. **IPs hardcodeadas** — al reiniciar un servicio cambiaba su IP y los
   demás no lo encontraban (falacia "la topología no cambia" del TP1, en
   vivo).

Este TP2 implementa un **subconjunto representativo** (catálogo + pedidos
+ notificaciones) que demuestra cómo se resuelven con: contenedores
(Docker), orquestación (Kubernetes), comunicación interna eficiente
(gRPC) y desacoplamiento temporal (RabbitMQ).

> El TP explícitamente desaconseja implementar los 5 servicios: "mejor
> dos sólidos que cinco a medias". Los servicios no implementados
> (inventario, pagos) se *justifican* en este informe sin codearlos.

---

## 2. Arquitectura implementada

### 2.1 Mapa de servicios

```
                          [Frontend / curl]
                                  │  REST POST /orders
                                  ▼
                          ┌───────────────┐         gRPC sync
                          │    Pedidos    │ ──────  ConsultarStock ──┐
                          │  (FastAPI)    │       (timeout 300ms)    │
                          │   2 replicas  │                          ▼
                          └───────┬───────┘                  ┌──────────────┐
                                  │  AMQP publish            │   Catalogo   │
                                  │  (durable, persistent)   │   (gRPC)     │
                                  ▼                          │  2 replicas  │
                          ┌───────────────┐                  └──────────────┘
                          │   RabbitMQ    │
                          │ (durable: SPOF)│
                          └───────┬───────┘
                                  │
                                  ▼
                          ┌───────────────┐
                          │ Notificaciones│
                          │  (consumer)   │
                          │  idempotente  │
                          └───────────────┘
```

### 2.2 Responsabilidades

| Servicio | Stack | API expone | Eventos publica | Eventos consume | Dueño de |
|---|---|---|---|---|---|
| Catálogo | Python · grpcio | gRPC: `ConsultarStock` | — | — | productos, stock (mock) |
| Pedidos | Python · FastAPI | REST: `POST /orders`, `GET /health`, `GET /ready` | `emails` (cola) | — | pedidos |
| Notificaciones | Python · pika | — | — | `emails` | log de envíos (mock) |

Inventario y pagos no se implementan; en una iteración real:

- **Inventario** expondría `gRPC: ReservarStock / LiberarStock`. El flujo
  sync de pedidos → inventario es el que mantiene la **consistencia
  fuerte de stock** (evita el overselling del TP1).
- **Pagos** sería **100% async** porque depende de un proveedor externo
  con latencia impredecible. Se comunicaría con `correlation_id` para
  reconciliar respuestas.

---

## 3. Decisiones sync vs async, flujo por flujo

> No existe respuesta universal. La respuesta correcta minimiza el costo
> de falla del flujo específico.

### 3.1 Frontend → Pedidos · **REST sync (HTTP/JSON)**

**Por qué REST y no gRPC:** el cliente es un navegador o app móvil.
gRPC requiere HTTP/2 nativo y stubs por plataforma; REST + JSON funciona
en `curl`, `fetch`, Postman, sin generación de código. Para APIs
externas heterogéneas, REST sigue siendo la mejor herramienta.

**Sacrificio:** mayor overhead de serialización JSON y headers. Aceptable
porque el volumen externo es bajo comparado con el tráfico
interno.

### 3.2 Pedidos → Catálogo · **gRPC sync con timeout=300ms**

**Por qué sync:** la consulta de stock es **bloqueante por negocio** — sin
ese dato no se puede decidir si confirmar el pedido. No tiene sentido
publicar un evento y esperar.

**Por qué gRPC y no REST:**
- Contrato versionado en `.proto` (números de campo inmutables, errores
  de compatibilidad detectables en build-time, no en runtime).
- Binario sobre HTTP/2 (3–10× más chico que JSON + headers).
- `deadline` propagable entre saltos.

**Sacrificio — desacoplamiento temporal:** si catálogo cae, pedidos
falla. **Es aceptable** porque K8s reinicia el pod en segundos
(MTTR bajo), y el timeout de 300ms hace **fail-fast** sin acumular
workers colgados. Mejor un error inmediato que la cascade failure del
incidente del caso.

### 3.3 Pedidos → Notificaciones · **RabbitMQ async**

**Por qué async:** un email puede tardar 30s, 2 minutos, o caer al
spam — el usuario no espera por él. Si fuera sync, una lentitud en el
SMTP lentificaría toda la API de pedidos.

**Sacrificio — respuesta inmediata:** no sabemos *cuándo* exactamente
sale el email. Aceptable para un email transaccional.

**Garantías implementadas:**
- Cola `durable=True` → sobrevive reinicios del broker.
- `delivery_mode=2` en el publish → mensaje persiste en disco.
- `publisher confirms` activado → el publish solo retorna ok si el
  broker confirmó la escritura.
- Consumer con **ack manual** → si el worker muere antes del ack,
  RabbitMQ reenvía. Esto es **at-least-once delivery**.
- Consumer **idempotente** → cache de `order_id` procesados; un
  duplicado se acka y descarta. Esto convierte at-least-once en el
  **efecto exactly-once** desde la aplicación.

### 3.4 Tabla resumen

| Flujo | Protocolo | Propiedad sacrificada | Mitigación |
|---|---|---|---|
| Frontend → Pedidos | REST | Latencia/overhead | Caché HTTP, BFF (futuro) |
| Pedidos → Catálogo | gRPC sync | Desacoplamiento temporal | timeout=300ms, replicas≥2, autohealing K8s |
| Pedidos → Pagos *(no impl.)* | AMQP async | Respuesta inmediata | `correlation_id`, estado `PENDING` |
| Pedidos → Notificaciones | AMQP async | Inmediatez | Cola durable + ack manual + idempotencia |

---

## 4. Flujo "crear pedido" — paso a paso

```
[1]  Cliente       → Pedidos        REST POST /orders         (sync)
[2]  Pedidos       → Catálogo       gRPC ConsultarStock       (sync, deadline 300ms)
[3]  Pedidos       (valida stock)
[4]  Pedidos       → RabbitMQ       publish "emails"          (async, persistent)
[5]  Pedidos       → Cliente        HTTP 201 {order_id}       (sync, le contesta)
                                                            ──── desacople ────
[6]  Notificaciones ← RabbitMQ      consume "emails"
[7]  Notificaciones (chequea idempotencia → envia email simulado)
[8]  Notificaciones → RabbitMQ      basic_ack
```

El usuario recibe `201` en el paso 5, **antes** de que el email se envíe.
Es el patrón "respuesta optimista" + "consistencia eventual": estado
final converge en milisegundos a segundos.

---

## 5. SPOFs y mitigaciones

Partir el monolito **elimina** algunos SPOFs (un bug en un servicio ya
no tumba a los otros) pero **crea tres nuevos**:

### 5.1 Broker (RabbitMQ)
**Riesgo:** si RabbitMQ cae, pedidos no puede publicar `emails`.
**Detección:** failed publish → 502 al cliente, log de error.
**Mitigación:**
- **Cluster RabbitMQ** de 3 nodos con quorum queues (resiste 1 caída).
- **Outbox pattern**: pedidos escribe el evento a una tabla local
  (`outbox`) dentro de la misma transacción que el pedido. Un proceso
  separado lee la outbox y publica con reintentos. Si el broker está
  caído, los eventos se acumulan en la outbox sin perder datos.

### 5.2 Plano de control de Kubernetes
**Riesgo:** si el `kube-apiserver` o `etcd` caen, no hay scheduling ni
auto-healing. Los pods que ya corren siguen funcionando (bueno), pero
si uno muere nadie lo reemplaza.
**Mitigación:** usar K8s **administrado HA** (GKE, EKS, AKS), donde el
plano de control tiene SLA del proveedor y es transparente al equipo.

### 5.3 Red interna y DNS del cluster
**Riesgo:** servicios que antes compartían memoria ahora dependen de la
red. Un `kube-dns` saturado o una `NetworkPolicy` mal puesta rompen toda
la comunicación interna. Es un SPOF **nuevo** que no existía en el
monolito.
**Mitigación:** monitoreo dedicado de `kube-dns`, políticas de red
testeadas en staging antes de prod, **retries con backoff exponencial +
jitter** en los clientes para tolerar transitorios.

---

## 6. Cuellos de botella nuevos

| Cuello | Síntoma | Mitigación |
|---|---|---|
| **Cascadas sync profundas** | Latencia P99 = suma de saltos | No más de 2 saltos sync por request de usuario. Paralelizar con `asyncio.gather`. |
| **Cola sin consumer** | Cola crece hasta llenar disco | Alerta sobre tamaño de cola; HPA del consumer por `queue_depth`; **DLQ con TTL**. |
| **Serialización JSON interno** | CPU alto, latencia gratuita | gRPC + Protobuf interno (F-07 del TP1 resuelta). |
| **Debugging distribuido** | Bug = correlar 4 servicios + cola | `correlation_id` propagado (implementado); tracing distribuido a futuro. |

---

## 7. Patrones defensivos (resiliencia)

Aplicados o documentados en este TP:

| Patrón | Estado | Dónde |
|---|---|---|
| **Timeout + deadline** | ✅ implementado | `pedidos/main.py` (`GRPC_TIMEOUT=0.3`) |
| **Idempotencia consumer** | ✅ implementado | `notificaciones/worker.py` (set `procesados`) |
| **Publisher confirms** | ✅ implementado | `pedidos/main.py` (`ch.confirm_delivery()`) |
| **Liveness ≠ Readiness** | ✅ implementado | `k8s/pedidos.yaml` (`/health` vs `/ready`) |
| **Retry con backoff + jitter** | ⚠️ parcial | Solo reconexión del consumer (`time.sleep(3)`). En prod: backoff exponencial. |
| **Circuit Breaker** | 📋 documentado | Sería `pybreaker` envolviendo la llamada gRPC en pedidos. |
| **Bulkhead** | 📋 documentado | Pools de threads/conexiones separados por dependencia. |
| **Rate Limiting** | 📋 documentado | API Gateway con token bucket por usuario. |

---

## 8. Propuestas arquitectónicas (TP3+)

Estas mejoras **no se implementan** en TP2 — se describen para mostrar
la dirección de madurez del sistema.

### 8.1 API Gateway / BFF
Un único punto de entrada público (Kong / Traefik / Envoy) que enrutaría
al servicio correcto. Centraliza auth, rate limiting, logs. Los
servicios internos no quedan expuestos a Internet. Aplica
**Backend-for-Frontend** si el frontend móvil necesita un shape
distinto al web.

### 8.2 Outbox pattern (resuelve dual-write)
Hoy `pedidos` hace dos escrituras independientes: la DB local y el
`basic_publish` a RabbitMQ. Si la primera funciona y la segunda falla
(broker caído), el evento se pierde. El outbox lo resuelve:

```sql
BEGIN;
  INSERT INTO orders (...);
  INSERT INTO outbox (event_type, payload) VALUES ('email_requested', ...);
COMMIT;
```

Un publisher dedicado lee `outbox` y publica con reintentos. La DB es
la fuente de verdad — si el broker estuvo caído 10 minutos, se publica
todo lo pendiente cuando vuelve.

### 8.3 Distributed Tracing (OpenTelemetry + Jaeger)
Cada request recibe un `trace_id` y los servicios reportan spans con
duración y atributos. Permite visualizar el flujo "crear pedido"
completo en un timeline. Hoy tenemos `correlation_id` (logs); el paso
siguiente es tracing estructurado.

### 8.4 Service Mesh (Istio / Linkerd)
Inyecta un sidecar en cada pod que maneja TLS mutuo, retry, timeout,
circuit breaker, métricas y tracing **sin tocar el código de
aplicación**. Lo que hoy se hace manualmente en cada servicio, el mesh
lo resuelve a nivel plataforma.

### 8.5 Saga pattern (transacciones distribuidas — TP3)
El flujo completo "reservar stock → cobrar → confirmar orden → notificar"
es una saga con compensaciones (si el paso 2 falla, liberar stock del
paso 1). Sin ACID global, la consistencia es eventual.

---

## 9. SLI / SLO / SLA aplicado a MPI

| Sigla | Ejemplo concreto MPI |
|---|---|
| **SLI** (Indicator) | % de `POST /orders` que responden en <500ms. |
| **SLO** (Objective) | 99.5% mensual, medido a partir del SLI anterior. |
| **SLA** (Agreement) | Compromiso contractual al cliente: 99% mensual o reembolso. |

Relación práctica: **SLA < SLO < SLI real**. El SLA público se publica
*más bajo* que el SLO interno para tener margen.

**Error budget:** con SLO 99.5%, hay 0.5% mensual "permitido" de errores
(~3.6 horas en un mes de 30 días). Mientras no se agote, se pueden
desplegar features riesgosas. Si se agota, *se frenan las features* y
se trabaja en confiabilidad. Estructura el equilibrio velocidad vs
estabilidad.

---

## 10. Conclusiones

Tres conclusiones que vale la pena retener:

1. **Microservicios no es un destino, es una caja de herramientas.**
   Partir el monolito sin contenedores + K8s + service discovery +
   comunicación pensada para distribuido replica los problemas a la
   capa de red. El TP2 muestra el "set mínimo" para que la separación
   valga la pena.

2. **Sync vs async no es ideológico — se decide por flujo.**
   Catálogo es sync porque necesitamos el dato *ahora*; notificaciones
   es async porque el usuario no espera. Aplicar el mismo protocolo a
   todo es elegir mal en al menos un caso.

3. **La idempotencia es responsabilidad de la aplicación.**
   Ningún broker garantiza "exactly-once" gratis. RabbitMQ ofrece
   at-least-once + persistencia; el "efecto exactly-once" lo construye
   el consumer guardando IDs procesados. Si esto se omite, los emails
   se duplican apenas haya el primer crash. Es el error #1 detectado en
   los snippets generados por IA.
