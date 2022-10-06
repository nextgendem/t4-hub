from fastapi.testclient import TestClient
from .main import app

client = TestClient(app)
test_user ={}

def test_read_root():
    response = client.get("/redirect_example")
    assert response.status_code == 200


def test_launch_container():
    response = client.post(
        "/login",
        json=dict(username="user", password="pass")
    )
    assert response.status_code == 200




