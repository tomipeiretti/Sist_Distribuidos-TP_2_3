import concurrent.futures
import time

import requests


BASE_URL = "http://localhost:8001"


def set_stock(sku: str, cantidad: int):
    response = requests.post(
        f"{BASE_URL}/stock",
        json={"sku": sku, "cantidad": cantidad},
        timeout=2,
    )
    assert response.status_code == 200


def get_stock(sku: str) -> int:
    response = requests.get(
        f"{BASE_URL}/stock/{sku}",
        timeout=2,
    )
    assert response.status_code == 200
    return response.json()["stock"]


def reserve(sku: str, cantidad: int):
    return requests.post(
        f"{BASE_URL}/reserve",
        json={"sku": sku, "cantidad": cantidad},
        timeout=2,
    )


def test_dos_usuarios_un_producto():
    sku = "TEST-001"
    set_stock(sku, 1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(reserve, sku, 1),
            executor.submit(reserve, sku, 1),
        ]

        responses = [future.result() for future in futures]

    status_codes = [response.status_code for response in responses]

    assert status_codes.count(200) == 1
    assert status_codes.count(400) + status_codes.count(503) == 1
    assert get_stock(sku) == 0


def test_cincuenta_usuarios_diez_productos():
    sku = "TEST-010"
    set_stock(sku, 10)

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [
            executor.submit(reserve, sku, 1)
            for _ in range(50)
        ]

        responses = [future.result() for future in futures]

    status_codes = [response.status_code for response in responses]

    assert status_codes.count(200) == 10
    assert status_codes.count(400) + status_codes.count(503) == 40
    assert get_stock(sku) == 0


def test_redis_responde_rapido():
    start = time.time()

    response = requests.get(
        f"{BASE_URL}/ready",
        timeout=2,
    )

    duration = time.time() - start

    assert response.status_code == 200
    assert duration < 2