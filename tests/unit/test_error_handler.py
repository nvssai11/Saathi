from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)

app = FastAPI()
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.get("/plain-detail")
async def _plain_detail():
    raise HTTPException(status_code=404, detail="Order 42 does not exist")


@app.get("/structured-detail")
async def _structured_detail():
    raise HTTPException(
        status_code=409,
        detail={"code": "ORDER_NOT_SETTLED", "message": "Order 42 is not yet CLOSED"},
    )


@app.get("/unmapped-status")
async def _unmapped_status():
    raise HTTPException(status_code=418, detail="I'm a teapot")


@app.get("/boom")
async def _boom():
    raise RuntimeError("something genuinely unexpected")


class _Body(BaseModel):
    quantity: int


@app.post("/validated")
async def _validated(body: _Body):
    return {"quantity": body.quantity}


client = TestClient(app, raise_server_exceptions=False)


def test_plain_string_detail_gets_generic_status_code():
    response = client.get("/plain-detail")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Order 42 does not exist"}
    }


def test_structured_detail_passed_through_as_is():
    response = client.get("/structured-detail")
    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "ORDER_NOT_SETTLED", "message": "Order 42 is not yet CLOSED"}
    }


def test_unmapped_status_falls_back_to_generic_error_code():
    response = client.get("/unmapped-status")
    assert response.status_code == 418
    assert response.json() == {"error": {"code": "ERROR", "message": "I'm a teapot"}}


def test_unhandled_exception_returns_documented_shape_not_raw_traceback():
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}
    }
    assert "something genuinely unexpected" not in response.text


def test_validation_error_gets_documented_shape_not_fastapi_default():
    response = client.post("/validated", json={"quantity": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "quantity" in body["error"]["message"]


def test_valid_request_still_works_normally():
    response = client.post("/validated", json={"quantity": 5})
    assert response.status_code == 200
    assert response.json() == {"quantity": 5}
