import json
from datetime import time

import docker
import time

from dotenv import load_dotenv

from fastapi.testclient import TestClient
import asyncio
from tsliceh.main import app, orm_session_maker, allowed_inactivity_time_in_seconds
from tsliceh import Session3DSlicer
import pytest
import os
import logging
from tsliceh.main import CONTAINER_NAME_PREFIX
data = {"username": "free_user", "password": "test"}


logger = logging.getLogger(__name__)


def remove_container():
    dc = docker.from_env()
    try:
        c = dc.containers.get(data["username"])
        try:
            c.stop()
        finally:
            c.remove()
    except Exception as e:
        logger.info(e.args)


def compare_time_min(file):
    file_mod_time = time.gmtime(os.path.getmtime(file))
    now = time.gmtime(time.time())
    return (file_mod_time.tm_mday, file_mod_time.tm_hour,file_mod_time.tm_min == now.tm_mday, now.tm_hour, now.tm_min)


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
            logger.info(e.args)
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
        status = dc.containers.get(CONTAINER_NAME_PREFIX + data["username"]).status
        assert status == "running"
    except AssertionError as error:
        print(error)


def test_delete_container_and_session(client):
    # login user
    load_dotenv()
    tic = time.perf_counter()
    response = client.post("/login", data)
    assert response.status_code == 302
    toc = time.perf_counter()
    logger.info(f"log new user in {toc - tic:0.4f} seconds")
    # is the container alive?
    dc = docker.from_env()
    try:
        status = dc.containers.get(CONTAINER_NAME_PREFIX + data["username"]).status
        assert status == "running"
    except AssertionError as error:
        logger.info(error)
    # will delete the container ahter innactivity? (time of inactivity + maximun witing time of check_session routine
    waiting = (allowed_inactivity_time_in_seconds + 60)
    logger.info(f"waiting for {waiting} s")
    # Todo not sure about using time.sleep or asyncio.sleep
    time.sleep(waiting)
    # any container?
    try:
        c = dc.containers.get(CONTAINER_NAME_PREFIX + data["username"])
    except:
        c = None
    assert c is None
    #  adding maximun witing time of check_session routine
    waiting = waiting + 60
    logger.info(f"waiting for {waiting} s")
    tic = time.perf_counter()
    time.sleep(waiting)
    # any 3DslicerSession?
    session = orm_session_maker()
    s = session.query(Session3DSlicer).filter(Session3DSlicer.user == data["username"]).first()
    assert s is None
    index_file = os.getenv("INDEX_PATH")
    nginx_conf_file = os.getenv("NGINX_CONFIG_FILE")
    assert compare_time_min(index_file)
    assert compare_time_min(nginx_conf_file)
    logger.info(f"index file rewriten at {time.ctime(os.path.getmtime(index_file))} current time is {time.ctime(time.time())} ")
    logger.info(f"index file rewriten at {time.ctime(os.path.getmtime(nginx_conf_file))} current time is {time.ctime(time.time())}")


def test_restart_session():
    # how to mock activity??????????
    pass

def test_create_volume(client):
    from tsliceh.volumes import volume_dict, vol_dict
    test_launch_container(client)
    dc = docker.from_env()
    volumes = volume_dict(data["username"])
    container = dc.containers.get(CONTAINER_NAME_PREFIX + data["username"])
    l = list()
    # "{f"{user}_{k}": {"bind": v, "mode": "rw"}})"
    for k,v in volumes.items():
        l.append(k + ":" + v["bind"] + ":" + v["mode"])
    l.sort()
    binds = container.attrs["HostConfig"]["Binds"]
    binds.sort()
    assert l == binds
