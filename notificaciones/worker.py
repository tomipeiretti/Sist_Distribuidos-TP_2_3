import json
import logging
import os
import sys
import time
from datetime import datetime

import pika

SERVICE_NAME = "notificaciones"
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@rabbitmq:5672/")
QUEUE = "emails"

# Idempotencia: cache de IDs ya procesados.
# En produccion: Redis o tabla DB con TTL para sobrevivir reinicios.
procesados: set[str] = set()


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


def enviar_email(order_id: str, sku: str):
    """Simulacion. En prod: SMTP, SendGrid, SES, etc."""
    log.info(f"[email enviado] orden={order_id} sku={sku}")


def on_message(ch, method, properties, body):
    cid = "-"
    if properties and properties.headers:
        cid = properties.headers.get("x-correlation-id", "-")

    try:
        payload = json.loads(body)
        order_id = payload["order_id"]
        sku = payload.get("sku", "?")
    except (json.JSONDecodeError, KeyError) as e:
        log.error(
            f"mensaje invalido descartado: {e}",
            extra={"correlation_id": cid},
        )
        # nack sin requeue: mensaje malformado, no tiene sentido reintentarlo.
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Idempotencia: si ya lo procesamos, ack y listo.
    # at-least-once de RabbitMQ + idempotencia local = efecto exactly-once.
    if order_id in procesados:
        log.info(
            f"[duplicado ignorado] order_id={order_id}",
            extra={"correlation_id": cid},
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    enviar_email(order_id, sku)
    procesados.add(order_id)

    # ACK MANUAL: recien ahora RabbitMQ borra el mensaje de la cola.
    # Si el worker muere antes de esta linea, RabbitMQ lo reentrega.
    ch.basic_ack(delivery_tag=method.delivery_tag)


def consume_forever():
    """Loop con reconexion ante caida del broker."""
    while True:
        try:
            log.info(f"conectando a {RABBIT_URL}")
            conn = pika.BlockingConnection(pika.URLParameters(RABBIT_URL))
            ch = conn.channel()
            ch.queue_declare(queue=QUEUE, durable=True)  # sobrevive reinicio del broker
            ch.basic_qos(prefetch_count=1)  # uno a la vez = fair dispatch
            # IMPORTANTE: sin auto_ack=True. El ack es manual en on_message.
            ch.basic_consume(queue=QUEUE, on_message_callback=on_message)
            log.info(f"consumiendo de '{QUEUE}'")
            ch.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelClosedByBroker) as e:
            log.warning(f"conexion perdida: {e}. reintentando en 3s")
            time.sleep(3)
        except KeyboardInterrupt:
            log.info("apagando consumer")
            return


if __name__ == "__main__":
    consume_forever()
