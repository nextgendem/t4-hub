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
from starlette.responses import RedirectResponse

from tsliceh import create_session_factory, create_local_orm, Session3DSlicer, create_tables, create_docker_network, \
    docker_compose_up
from helpers import get_container_ip, get_container_internal_adress, containers_status, containers_cpu_percent_dict, \
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
engine = create_local_orm("sqlite:////tmp/3h_sessions.sqlite")
create_tables(engine)
orm_session_maker = create_session_factory(engine)

load_dotenv()
nginx_container_name = os.getenv(
    'NGINX_NAME')  # TODO Read from environment variable the name of nginx container relative to this container
nginx_config_path = os.getenv(
    'NGINX_CONFIG_FILE')  # TODO Read from environment the location of nginx.conf relative to this container
index_path = os.getenv('INDEX_PATH')
allowed_inactivity_time_in_seconds = 600  # TODO Read from environment
network_name = os.getenv('NETWORK_NAME')
docker_compose_up()
network_id = create_docker_network(network_name)
tdslicerhub_adress = get_container_internal_adress(os.getenv("TDSLICERHUB_NAME"), network_id)
openldap_adress =  get_container_internal_adress(os.getenv("OPENLDAP_NAME"), network_id)


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
    return True  # TODO LDAP


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
                s.url_path = f"/x11/{s.uuid}/vnc.html?resize=remote&path=x11/{s.uuid}/websockify/"
                # Launch new container
                launch_3dslicer_web_docker_container(s)
                # Commit new
                session.add(s)
                session.commit()
                # Update nginx.conf and reread Nginx configuration
                refresh_html(session)
                refresh_nginx(session, nginx_config_path, nginx_container_name)
                sleep(3)
            return RedirectResponse(url=f"http://localhost{s.url_path}", status_code=302)  # TODO URL ??
        else:
            raise Exception(f"User {username} not authorized to open a 3DSlicer session")


def refresh_nginx(sess, nginx_cfg_path, nginx_cont_name):
    def generate_nginx_conf():
        """ For each session, generate a section, plus the first part """
        # TODO "nginx.conf" prefix
        _ = f"""
user www-data;

events {{
}}

http {{
  server {{
    listen     80;
    server_name  localhost;

    location / {{
      root   html;
      index  index.html index.htm;
    }}

    """
        for s in sess.query(Session3DSlicer).all():
            # TODO Section doing reverse proxy magic
            _ += f"""

    location /x11/{s.uuid}/ {{
          proxy_pass http://{s.service_address}/x11/;
        }}
    
    location /x11/{s.uuid}/websockify {{
      proxy_pass http://{s.service_address}/x11/websockify;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "Upgrade";
      proxy_set_header Host $host;
    }}        
    """
        _ += """
    }
}
        """
        if nginx_cfg_path:
            with open(nginx_cfg_path, "wt") as f:
                f.write(_)

    def command_nginx_to_read_configuration():
        """
        Given the name of the NGINX container used as reverse proxy for 3DSlicer sessions,
        command it to reread the configuration.
        """
        tries = 0
        while tries < 6:
            status = containers_status(nginx_container_name)
            if status == "running":
                dc = docker.from_env()
                nginx = dc.containers.get(nginx_container_name)
                r = nginx.exec_run("/etc/init.d/nginx reload")
                return r
            else:
                docker_compose_up()
                tries = +1
        raise Exception(500, "Error when reloading nginx.conf")

    # -----------------------------------------------

    generate_nginx_conf()
    command_nginx_to_read_configuration()


def refresh_html(sess):
    _ = """
<!DOCTYPE html>
<html>
<head>
<title>3DSlicer Sessions:</title>
</head>

    """
    for s in sess.query(Session3DSlicer).all():
        # TODO Section doing reverse proxy magic
        _ += f"""

<a href=http://localhost{s.url_path}>{s.user}</a><br>
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
    c = dc.containers.run(image="stevepieper/slicer-chronicle:4.10.2", ports={"8080/tcp": None}, name=container_name,
                          network=network_id, detach=True)
    container_id = c.id
    # wait until active:
    # active ornot..
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


def stop_docker_container(s: Session3DSlicer):
    """
    in certain session, stop container when:
    - session expires
    - order from user
    - order from administrator
    :param s: session
    :return:
    """
    # TODO MANAGE THOSE PRINTS
    dc = docker.from_env()
    c = dc.containers.get(s.container_name)
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
                print(f"can't stop container{s.container_name}")
    if c.status == "exited" or can_remove:
        c.remove()
        status = containers_status(s.container_name)
        if not status:
            print(f"container{s.container_name} : removed")
        else:
            print(f"can't remove {s.container_name}")


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
        container = s.container_name
        pct = docker_container_pct_activity(s.container_name)
        ahora = datetime.datetime.now()
        if pct > 10:
            s.last_activity = ahora
            stop = False
        else:
            stop = (ahora - s.last_activity).total_seconds() > allowed_inactivity_time_in_seconds
        return stop

    async def sessions_checker(self, sm):
        self.session_maker = sm
        # Start 3D Slicer sessions if we are back from a restart of the container
        sess = self.session_maker()
        for s in sess.query(Session3DSlicer).all():
            pct = docker_container_pct_activity(s.container_name)
            print(f"pct container: {s.container_name}: {pct} ")
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
                if stop:
                    stop_docker_container(s)
                    sess.delete(s)
                    # Update nginx.conf and reread Nginx configuration
                    refresh_nginx(sess, nginx_config_path, nginx_container_name)
                    refresh_html(sess)

            sess.commit()
            await asyncio.sleep(60)


runner = BackgroundRunner()


@app.on_event("startup")
async def startup():
    asyncio.create_task(runner.sessions_checker(orm_session_maker))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0")
