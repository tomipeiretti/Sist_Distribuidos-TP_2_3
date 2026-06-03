# IA Log — TP2 MPI Microservicios

Registro de interacciones con asistentes de IA durante el desarrollo del
TP, los errores típicos detectados y las correcciones aplicadas. Es
entregable obligatorio de la Estación 5 / Vibe Coding Lab.

> **Insight transversal:** la IA genera código que *parece* funcionar y
> corre sin errores aparentes en demo, pero esconde decisiones de
> *seguridad y producción* erradas. Funciona en demo, falla bajo carga
> real, mensajes duplicados, pods que mueren. La revisión humana sigue
> siendo obligatoria.

---

## Interacción 1 — Consumer de RabbitMQ

### Prompt
> "Dame un consumer Python de RabbitMQ que reciba mensajes de la cola
> `emails` y los imprima."

### Resumen de lo generado
La IA produjo un consumer compacto:

```python
import pika
conn = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
ch = conn.channel()
ch.queue_declare(queue="emails")
def cb(ch, method, props, body):
    print("recibido:", body)
ch.basic_consume(queue="emails", on_message_callback=cb, auto_ack=True)
ch.start_consuming()
```

### Errores detectados

1. **`auto_ack=True`** — el broker considera el mensaje entregado *apenas
   lo manda al consumer*, no después de procesarlo. Si el worker
   muere a mitad de procesamiento, **el mensaje se pierde**.
2. **`queue_declare` sin `durable=True`** — si el broker reinicia, la
   cola se borra (y los mensajes con ella).
3. **`host="localhost"`** hardcodeado — no funciona en Docker / K8s donde
   el broker está en otro pod (debería ser env var).
4. **Sin `basic_qos(prefetch_count=...)`** — el broker entrega todos los
   mensajes pendientes al primer consumer, desbalanceando la carga
   cuando hay réplicas.
5. **Sin manejo de idempotencia** — con at-least-once, el mismo mensaje
   puede llegar 2 veces. Si "procesar" tiene efecto acumulativo
   (descontar puntos, enviar email), aparecen duplicados.
6. **Sin reconexión ante caída del broker** — un `AMQPConnectionError` y
   el worker muere para siempre.

### Corrección aplicada
Ver `notificaciones/worker.py`:
- `auto_ack` eliminado; ack manual con `ch.basic_ack(delivery_tag=...)`
  después de procesar.
- `queue_declare(queue="emails", durable=True)`.
- `RABBIT_URL` desde env var.
- `basic_qos(prefetch_count=1)`.
- Cache `procesados` para chequear `order_id` antes de ejecutar efectos.
- Loop con `try/except pika.exceptions.AMQPConnectionError` y reintento.

---

## Interacción 2 — Cliente gRPC en Python

### Prompt
> "Genera el cliente Python para llamar el método `ConsultarStock` del
> servicio gRPC `Catalogo`."

### Resumen de lo generado

```python
import grpc
import catalogo_pb2, catalogo_pb2_grpc
channel = grpc.insecure_channel("catalogo:50051")
stub = catalogo_pb2_grpc.CatalogoStub(channel)
response = stub.ConsultarStock(catalogo_pb2.StockRequest(sku="SKU-001"))
print(response)
```

### Errores detectados

1. **Sin `timeout`** — si el server está colgado, el cliente espera
   *para siempre*. 100 requests + server caído = 100 workers
   bloqueados = OOM. Es exactamente el problema F-04 del TP1
   reproducido en gRPC.
2. **`channel` sin cerrar** — leak de file descriptors / conexiones
   HTTP/2 si se llama muchas veces.
3. **Sin `try/except grpc.RpcError`** — cuando algo falla, la excepción
   sube cruda al endpoint HTTP y se devuelve 500 al usuario en vez de
   un 503 controlado.
4. **Sin propagación de `correlation_id`** — el log del servidor no
   puede aparearse con la request original.

### Corrección aplicada
Ver `pedidos/main.py::_consultar_stock`:
- `timeout=GRPC_TIMEOUT` (0.3s configurable por env var).
- `with grpc.insecure_channel(...) as channel:` para cerrar
  determinísticamente.
- `try/except grpc.RpcError` → HTTP 503 con detalle.
- `metadata=(("x-correlation-id", cid),)` propagado en cada llamada.

---

## Interacción 3 — Dockerfile para FastAPI

### Prompt
> "Hace un Dockerfile para mi app FastAPI en Python."

### Resumen de lo generado

```dockerfile
FROM python:latest
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
CMD ["uvicorn", "main:app", "--host", "0.0.0.0"]
```

### Errores detectados

1. **`python:latest`** — imagen no reproducible, puede cambiar entre
   builds y romper en prod sin cambio de código.
2. **`COPY . /app` antes de `pip install`** — invalida el caché de
   capas cada vez que cambia *cualquier* línea de código. Builds 10×
   más lentos.
3. **Corre como `root`** — vulnerabilidad de seguridad clásica. Si
   alguien explota la app, tiene root en el contenedor (en algunos
   escapes, root del host).
4. **Sin `HEALTHCHECK`** — el orquestador no puede detectar zombies.
5. **`pip install` sin `--no-cache-dir`** — caché de pip se queda en la
   imagen, ~50–200MB extra.

### Corrección aplicada
Ver `pedidos/Dockerfile`:
- `FROM python:3.11-slim` (versión fija + variante slim).
- `COPY requirements.txt .` antes del `pip install`, `COPY ...py .`
  después.
- `--no-cache-dir`.
- `RUN useradd -m -u 1000 appuser` + `USER appuser`.
- `HEALTHCHECK` con `urllib.request.urlopen(...)`.
- `COPY --chown=appuser:appuser ...`.

---

## Interacción 4 — Manifiesto Deployment de Kubernetes

### Prompt
> "Dame un Deployment de Kubernetes para una app que escucha en el
> puerto 8000."

### Resumen de lo generado

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  replicas: 1
  selector:
    matchLabels: { app: my-app }
  template:
    metadata:
      labels: { app: myapp }       # ← ojo: app vs my-app
    spec:
      containers:
        - name: app
          image: myapp:latest
          ports: [ { containerPort: 8000 } ]
```

### Errores detectados

1. **Selector mismatch** — `selector.matchLabels` dice `app: my-app`,
   pero el template etiqueta con `app: myapp`. El Deployment "crea"
   pods pero **no los reconoce como propios** → `replicas` queda en
   `0/1` permanentemente y nadie entiende por qué. Error sutil y
   frecuente.
2. **`image: myapp:latest`** — además de no reproducible, K8s no
   sabe cuándo pullear: con `latest` el `imagePullPolicy` default es
   `Always`, lo cual rompe `kind` donde la imagen está local.
3. **Sin `resources.requests / limits`** — F-08 del TP1 trasplantada a
   K8s: un pod puede consumir toda la memoria del nodo y matar a los
   demás (OOM en cascada).
4. **Sin `livenessProbe` ni `readinessProbe`** — K8s no puede
   detectar zombies ni dependencias caídas.
5. **`replicas: 1`** — sin HA. Si muere ese pod, hay downtime hasta el
   restart.

### Corrección aplicada
Ver `k8s/pedidos.yaml`:
- Labels consistentes entre `selector` y `template`.
- `image: mpi/pedidos:v1` + `imagePullPolicy: IfNotPresent`.
- `resources.requests` y `resources.limits` definidos.
- `livenessProbe` HTTP `/health` (chequea solo proceso).
- `readinessProbe` HTTP `/ready` (chequea catálogo + broker).
- `replicas: 2`.

---

## Interacción 5 — "Optimizá este .proto"

### Prompt
> "Ordená este `.proto` por orden alfabético de campos para que sea más
> legible."

### Lo que iba a generar (interceptado a tiempo)

La IA propuso renumerar los campos para que `disponible = 1`,
`precio = 2`, `stock = 3`, `sku = 4`, "porque así quedan en orden
alfabético".

### Error detectado

**Cambiar números de campo rompe a todos los clientes ya deployados.**
Los números son el *orden binario* del mensaje — los clientes viejos
seguirán interpretando los bytes en las posiciones originales y leerán
basura (un `string` donde había un `int32`).

Las reglas inmutables de Protobuf:
- **NUNCA** cambiar el número de un campo existente.
- **NUNCA** cambiar el tipo.
- **NUNCA** reutilizar un número que se borró (usar `reserved N;`).
- Sí podés renombrar el campo (el nombre no viaja en el binario), o
  agregar campos nuevos con números nuevos.

### Resolución
Se rechazó la "optimización" y se dejó el `.proto` con los números
originales. Comentario explicativo agregado al archivo
`catalogo/catalogo.proto`.

---

## Resumen de aprendizajes

| Categoría | Error típico de IA | Cómo detectarlo |
|---|---|---|
| **Mensajería** | `auto_ack=True`, queue no durable | "Si el worker muere a mitad, ¿se pierde el mensaje?" |
| **gRPC** | Sin timeout | "Si el server tarda 30s, ¿qué pasa con el cliente?" |
| **Dockerfile** | `:latest`, corre como root, sin HEALTHCHECK | Leer línea por línea con la rúbrica en la mano |
| **K8s** | Selector mismatch, sin resources, `replicas: 1` | `kubectl apply --dry-run=client` y `kubectl describe` |
| **Protobuf** | Renumerar campos "para ordenar" | Memorizar las reglas inmutables |

**La IA es buena para boilerplate (estructura del `.proto`, esqueleto
de FastAPI, sintaxis YAML). Es mala como revisor de consistencia
distribuida.** Usarla así.
