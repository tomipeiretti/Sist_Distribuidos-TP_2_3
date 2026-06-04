import random

from locust import HttpUser, between, task


class UsuarioCompra(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def reservar_producto(self):
        self.client.post(
            "/reserve",
            json={
                "sku": "SKU-001",
                "cantidad": 1
            }
        )