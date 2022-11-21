"""
3DSlicer Hub aims to imitate the functionality of JupyterHub, but for 3DSlicer:
  - Provide a login mechanism and a login page (ideally connected to an LDAP server)
  - Provide a way to launch 3DSlicer instances (in the future with a specific configuration)
  - Stop unused 3DSlicer instances
  - Integrate with a reverse proxy providing a single entry point for the users
  - Provide a way to share 3DSlicer instances
  - Persistent storage for new containers

  Documentation:
- https://docker-py.readthedocs.io/en/stable/
"""
import asyncio
import datetime
import os
from time import sleep

from dotenv import load_dotenv

import docker
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
import python_on_whales as docker_ow
import ldap3
from ldap3.core.exceptions import LDAPException
from tsliceh import create_session_factory, create_local_orm, Session3DSlicer, create_tables, create_docker_network, \
    docker_compose_up, refresh_nginx, pull_tdslicer_image, get_ldap_adress, get_domain_name
from tsliceh.helpers import get_container_ip, get_container_internal_adress, containers_status, \
    containers_cpu_percent_dict, \
    container_stats, calculate_cpu_percent

app = FastAPI(root_path="")
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.getenv("STATIC_FOLDER")), name="static")
engine = create_local_orm("sqlite:////tmp/3h_sessions.sqlite")
create_tables(engine)
orm_session_maker = create_session_factory(engine)

load_dotenv()
nginx_container_name = os.getenv(
    'NGINX_NAME')  # TODO Read from environment variable the name of nginx container relative to this container
nginx_config_path = os.getenv(
    'NGINX_CONFIG_FILE')  # TODO Read from environment the location of nginx.conf relative to this container
index_path = os.getenv('INDEX_PATH')
allowed_inactivity_time_in_seconds = 120  # TODO Read from environment
network_name = os.getenv('NETWORK_NAME')
domain = get_domain_name(os.getenv("MODE"), os.getenv('DOMAIN'))
# docker_compose_up()
network_id = create_docker_network(network_name)
tdslicerhub_adress = get_container_internal_adress(os.getenv("TDSLICERHUB_NAME"), network_id)
ldap_adress = get_ldap_adress(os.getenv("MODE"), os.getenv("OPENLDAP_NAME"), network_id)
tdslicer_image_tag = "5.0.3"
tdslicer_image_name = "stevepieper/slicer-chronicle"
ldap_base = "ou=jupyterhub,dc=opendx,dc=org"
refresh_nginx(None, nginx_config_path, nginx_container_name)


# Welcome & login page
@app.get("/login")
async def welcome_and_login_page(request: Request):
    # Jinja2 template with login page
    _ = dict(request=request)
    return templates.TemplateResponse("login.html", _)


@app.get("/")
async def user_index_page(request: Request):
    # Jinja2 template with a simple redirect page
    _ = dict(request=request)
    return templates.TemplateResponse("index.html", _)


@app.get("/redirect_example")
async def redirect_example(request: Request):
    # Jinja2 template with a simple redirect page
    _ = dict(request=request)
    return templates.TemplateResponse("dummy.html", _)


async def check_credentials(user, password):
    try:
        with ldap3.Connection(ldap_adress, user=f"uid={user},{ldap_base}", password=password,
                              read_only=True) as conn:
            print(conn.result["description"])  # "success" if bind is ok
            return True
    except LDAPException:
        print('Unable to connect to LDAP server')
        return False


async def can_open_session(user):
    return True  # TODO LDAP


# Start (or resume) 3DSlicer session
@app.post("/login")
async def login(login_form: OAuth2PasswordRequestForm = Depends()):
    username = login_form.username
    password = login_form.password
    session = orm_session_maker()
    if await check_credentials(username, password):
        if await can_open_session(username):
            s = session.query(Session3DSlicer).filter(Session3DSlicer.user == username).first()
            if not s:
                s = Session3DSlicer()
                s.user = username
                s.info = {}
                s.last_activity = datetime.datetime.now()
                session.add(s)
                session.flush()
                s.url_path = f"/x11/{s.uuid}/vnc.html?resize=scale&autoconnect=true&path=x11/{s.uuid}/websockify"
                # Launch new container
                launch_3dslicer_web_docker_container(s)
                pct = docker_container_pct_activity(s.container_name)
                s.info = {'CPU_pct': f'{pct}'}
                # Commit new
                session.add(s)
                session.commit()
                # Update nginx.conf and reread Nginx configuration
                refresh_html(session)
                refresh_nginx(session, nginx_config_path, nginx_container_name)
                sleep(3)
            return RedirectResponse(url=f"http://{domain}{s.url_path}", status_code=302)  # TODO URL ??
        else:
            raise Exception(f"User {username} not authorized to open a 3DSlicer session")


def refresh_html(sess):
    _ = """
<!DOCTYPE html>
<html>
<head>
<title>3DSlicer Sessions:</title>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://www.w3schools.com/w3css/4/w3.css">
<link rel="stylesheet" href="https://www.w3schools.com/lib/w3-theme-black.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
</head>
<body id="myPage">
<!-- Image Header -->
<div class="w3-display-container w3-animate-opacity">
  <img src="/static/images/logo_y_titulo_fondo_naranja.png" alt="logo_opendx28" style="width:100%;min-height:350px;max-height:600px;">
<!--  <div class="w3-container w3-display-bottomleft w3-margin-bottom">
    <button onclick="document.getElementById('id01').style.display='block'" class="w3-button w3-xlarge w3-theme w3-hover-teal" title="Go To W3.CSS">LEARN W3.CSS</button>
  </div>-->
</div>

    """
    for s in sess.query(Session3DSlicer).all():
        # TODO Section doing reverse proxy magic
        _ += f"""
<div class="w3-quarter">
<a href=http://{domain}{s.url_path} target="_blank" rel="noopener noreferrer">
    <img src="/static/images/3dslicer.png" alt="3dslicerImagesNotFound" style="width:45%" class="w3-circle w3-hover-opacity">
</a>
<h3>{s.user}</h3>
<p> {s.info} % </p>
<p> Last Activity: {s.last_activity}</p>
</div>
    """
    _ += f"""
<div class="w3-quarter">
<a href="http://{domain}/login" target="_blank" rel="noopener noreferrer">
    <img src="../static/images/3dslicer.png" alt="3dslicerImagesNotFound" style="width:45%" class="w3-circle w3-hover-opacity">
</a>    
   <h3>
   <a href="http://{domain}/login" target="_blank" rel="noopener noreferrer">New Session</a>
   </h3>
</div>

</body>
    """
    if index_path:
        with open(index_path, "wt") as f:
            f.write(_)


def launch_3dslicer_web_docker_container(s: Session3DSlicer):
    """
    Launch a 3DSlicer web container
    """
    # docker client:
    active = False
    dc = docker.from_env()
    # just a container per user
    container_name = s.user
    print("::::::::::::::::::::::::CREATING NEW CONTAINER:::::::::::::::::::::::::::::::::")
    pull_tdslicer_image(tdslicer_image_name, tdslicer_image_tag)
    c = dc.containers.run(image=f"{tdslicer_image_name}:{tdslicer_image_tag}", ports={"8080/tcp": None},
                          name=container_name,
                          network=network_id, detach=True)
    container_id = c.id
    # wait until active:
    # active or not..
    while not active:
        # TODO mejorar: crear funcion check_state
        sleep(3)
        c = dc.containers.get(container_id)
        if c.status == "running":
            active = True
        if c.status == "exited":
            print("container exited")
            break
    logs = c.logs
    # todo error control
    s.service_address = get_container_internal_adress(c.id, network_id)
    s.container_name = container_name
    print(
        f"::::::::::::::::::::::::::container {c.name} : {c.status} in {s.service_address}::::::::::::::::::::::::::::::::::::")


def stop_docker_container(name):
    """
    in certain session, stop container when:
    - session expires
    - order from user
    - order from administrator
    :param name:
    :return:
    """
    # TODO MANAGE THOSE PRINTS
    dc = docker.from_env()
    try:
        c = dc.containers.get(name)
        can_remove = False
        status = containers_status(c.id)
        if status:
            if status == "running":
                try:
                    c.stop()
                    c.reload()
                    can_remove = True
                except:
                    can_remove = False
                    print(f"can't stop container{name}")
        if c.status == "exited" or can_remove:
            c.remove()
            status = containers_status(name)
            if not status:
                print(f"::::::::::::::::::::::::::::::container{name} : removed::::::::::::::::::::::::::::::::::")
            else:
                print(f":::::::::::::::::::::::::::::::::::::::can't remove {name}:::::::::::::::::::::::::::::::")
    except:
        print(f"::::::::::::::::::::::::::::::::::::::: {name} already removed:::::::::::::::::::::::::::::::")


def docker_container_pct_activity(container_id_name):
    """
    Obtain the percentage of activity of a container
    -1 if the container does not exist

    :param container_id_name: container id or name
    :return: -c if such container does not exist or real cpu percentage
    """
    dc = docker.from_env()
    try:
        c = dc.containers.get(container_id_name)
        stats = container_stats(c.id)
        return calculate_cpu_percent(stats)

    except:
        return -1


class BackgroundRunner:
    def __init__(self):
        self.session_maker = None

    async def check_session_activity(self, s: Session3DSlicer, db_sess):
        print(":::::::::::::::::::::::Checking Session Activity:::::::::::::::::::::::::::::::::::")
        container = s.container_name
        pct = docker_container_pct_activity(s.container_name)
        print(f"pct container: {s.container_name}: {pct} ")
        s.info = {'CPU_pct': f'{pct}'}
        ahora = datetime.datetime.now()
        if pct > 10:
            s.last_activity = ahora
            stop = False
        else:
            stop = (ahora - s.last_activity).total_seconds() > allowed_inactivity_time_in_seconds
        return stop

    async def sessions_checker(self, sm):
        print(":::::::::::::::::::::::Session Checker:::::::::::::::::::::::::::::::::::")
        self.session_maker = sm
        # Start 3D Slicer sessions if we are back from a restart of the container
        sess = self.session_maker()
        for s in sess.query(Session3DSlicer).all():
            pct = docker_container_pct_activity(s.container_name)
            print(f"pct container: {s.container_name}: {pct} ")
            s.info = {'CPU_pct': f'{pct}'}
            sess.add(s)
            if pct < 0:
                if s.restart:
                    launch_3dslicer_web_docker_container(s)
                else:
                    sess.delete(s)

        sess.commit()
        # Update nginx.conf and reread Nginx configuration
        refresh_nginx(sess, nginx_config_path, nginx_container_name)
        refresh_html(sess)

        while True:
            sess = self.session_maker()
            # Loop all containers
            for s in sess.query(Session3DSlicer).all():
                stop = await self.check_session_activity(s, sess)
                sess.add(s)
                if stop:
                    stop_docker_container(s.container_name)
                    sess.delete(s)
                    # Update nginx.conf and reread Nginx configuration
                    refresh_nginx(sess, nginx_config_path, nginx_container_name)
                    refresh_html(sess)

            sess.commit()
            await asyncio.sleep(60)

    async def delete_lost_containers(self, sm):
        self.session_maker = sm
        sess = self.session_maker()
        dc = docker.from_env()
        try:
            compose_containers = [c.name for c in docker_ow.compose.ps()]
            tdslicer_containers = [c.name for c in dc.containers.list(all)]
        except:
            return None
        users = [sess.user for s in sess.query(Session3DSlicer).all()]
        for c in compose_containers:
            if compose_containers in tdslicer_containers:
                tdslicer_containers.remove(compose_containers)
        for name in tdslicer_containers:
            stop_docker_container(name)
            await asyncio.sleep(200)


runner = BackgroundRunner()


@app.on_event("startup")
async def startup():
    asyncio.create_task(runner.sessions_checker(orm_session_maker))
    asyncio.create_task(runner.delete_lost_containers(orm_session_maker))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")
