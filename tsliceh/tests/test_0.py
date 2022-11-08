import json
from datetime import time

# from fastapi.testclient import TestClient
# from sqlalchemy import create_engine
#
# from tsliceh.main import app, orm_session_maker
import docker
import time
from tsliceh.main import BackgroundRunner, orm_session_maker
from fastapi import FastAPI
from fastapi.testclient import TestClient
import asyncio
from tsliceh.main import app, orm_session_maker, allowed_inactivity_time_in_seconds
from tsliceh import create_tables, create_session_factory, create_local_orm, Session3DSlicer
import pytest
import pytest_asyncio

data = {"username": "juan_ruiz", "password": "prueba"}


def remove_container():
    dc = docker.from_env()
    try:
        c = dc.containers.get(data["username"])
        try:
            c.stop()
        finally:
            c.remove()
    except Exception as e:
        print(e.message, e.args)


@pytest.fixture(autouse="module")
def clean_user_container():
    yield
    session = orm_session_maker()
    s = session.query(Session3DSlicer).filter(Session3DSlicer.user == data["username"]).first()
    if s:
        try:
            session.delete(s)
            session.commit()
        except Exception as e:
            print(e.message, e.args)
        finally:
            remove_container()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# Configures the event loop to be created only once per module
@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def test_read_root(client):
    response = client.get("/")
    assert response.status_code == 200


def test_launch_container(client):
    response = client.post("/login", data)
    assert response.status_code == 302
    dc = docker.from_env()
    try:
        status = dc.containers.get(data["username"]).status
        assert status == "running"
    except AssertionError as error:
        print(error)


def test_delete_container_and_session(client):
    response = client.post("/login", data)
    assert response.status_code == 302
    dc = docker.from_env()
    try:
        status = dc.containers.get(data["username"]).status
        assert status == "running"
    except AssertionError as error:
        print(error)
    waiting = (allowed_inactivity_time_in_seconds + 60)
    print(f"waiting for {waiting} s")
    # Todo not sure about using time.sleep or asyncio.sleep
    time.sleep(waiting)
    try:
        c = dc.containers.get(data["username"])
    except:
        c = None
    assert c is None
    waiting = waiting + 60
    print(f"waiting for {waiting} s")
    time.sleep(waiting)
    session = orm_session_maker()
    s = session.query(Session3DSlicer).filter(Session3DSlicer.user == data["username"]).first()
    assert s is None


def test_restart_session():
    # how to mock activity??????????
    pass
