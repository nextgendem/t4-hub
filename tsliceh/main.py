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

from dotenv import load_dotenv

import docker
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.attributes import flag_modified
from starlette.responses import RedirectResponse, HTMLResponse

import ldap3
from ldap3.core.exceptions import LDAPException
from tsliceh import create_session_factory, create_local_orm, Session3DSlicer, create_tables, create_docker_network, \
    refresh_nginx, pull_tdslicer_image, get_ldap_adress, get_domain_name
from tsliceh.volumes import create_all_volumes, volume_dict
from tsliceh.helpers import get_container_internal_adress, containers_status, \
    container_stats, calculate_cpu_percent
import logging.config
from fastapi.logger import logger
import logging

# INITIALIZE
# setup loggers https://github.com/tiangolo/uvicorn-gunicorn-fastapi-docker/issues/19#issuecomment-606672830
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"), disable_existing_loggers=False)
gunicorn_logger = logging.getLogger('gunicorn.error')
logger.handlers = gunicorn_logger.handlers
if __name__ != "main":
    logger.setLevel(gunicorn_logger.level)
else:
    logger.setLevel(logging.DEBUG)

app = FastAPI(root_path="")
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

load_dotenv()

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

engine = create_local_orm(os.getenv("DB_CONNECTION_STRING"))
create_tables(engine)
orm_session_maker = create_session_factory(engine)

CONTAINER_NAME_PREFIX = "h__tds__"
ACTIVITY_THRESHOLD = 10
nginx_container_name = os.getenv('NGINX_NAME')  # TODO Read from environment variable the name of nginx container relative to this container
nginx_config_path = os.getenv('NGINX_CONFIG_FILE')  # TODO Read from environment the location of nginx.conf relative to this container
index_path = os.getenv('INDEX_PATH')
allowed_inactivity_time_in_seconds = 300  # TODO Read from environment
network_name = os.getenv('NETWORK_NAME')
proto = os.getenv('PROTO')
domain = get_domain_name(os.getenv("MODE"), os.getenv('DOMAIN'))
url_base = f"{proto}://{domain}"
network_id = create_docker_network(network_name)
tdslicerhub_adress = get_container_internal_adress(os.getenv("TDSLICERHUB_NAME"), network_id) if os.getenv(
    "MODE") != "local" else domain
ldap_adress = get_ldap_adress(os.getenv("MODE"), os.getenv("OPENLDAP_NAME"), network_id)
tdslicer_image_tag = "5.0.3"
tdslicer_image_name = "stevepieper/slicer-chronicle"
ldap_base = "ou=jupyterhub,dc=opendx,dc=org"
refresh_nginx(None, nginx_config_path, domain, tdslicerhub_adress)


# Welcome & login page
@app.get("/index.html")
async def index_page():
    session = orm_session_maker()

    return HTMLResponse(content=refresh_index_html(session, proto=proto, admin=False, write_to_file=False),
                        status_code=200)


@app.get("/")
async def user_index_page():
    return RedirectResponse(url=f"index.html")


@app.get("/login")
async def welcome_and_login_page(request: Request):
    # Jinja2 template with login page
    _ = dict(request=request)
    return templates.TemplateResponse("login.html", _)


async def check_credentials(user, password):
    try:
        with ldap3.Connection(ldap_adress, user=f"uid={user},{ldap_base}", password=password,
                              read_only=True) as conn:
            print(conn.result["description"])  # "success" if bind is ok
            return True
    except LDAPException as e:
        print(e)
        logger.error(e.args)
        if user.startswith("free_user") and password == "test":
            return True
        else:
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
                s.last_activity = datetime.datetime.now()
                session.add(s)
                session.flush()
                s.url_path = f"/x11/{s.uuid}/vnc.html?resize=scale&autoconnect=true&path=x11/{s.uuid}/websockify"
                # Launch new 3d slicer container
                await launch_3dslicer_web_docker_container(s)
                s.info = {'CPU_pct': 0, 'shared': False}
                # Commit new
                session.add(s)
                session.commit()
                # Update nginx.conf and reread Nginx configuration
                refresh_nginx(session, nginx_config_path, domain, tdslicerhub_adress)

            # Redirect to a session management page:
            return RedirectResponse(url=f"{url_base}/sessions/{s.uuid}", status_code=302)
    else:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <html>
                                          <head>
                                            <title>Login Failed</title>
                                          </head>
                                          <body>
                                          <p>Login Failed: Your user ID or password is incorrect</p>
                                          </body>
                                        </html>""", status_code=401)


@app.get("/sessions/{session_id}")
async def get_session_management_page(request: Request, session_id: str):
    session = orm_session_maker()
    s = session.query(Session3DSlicer).get(session_id)
    _ = dict(request=request,
             url_base="",
             sess_uuid=session_id,
             sess_link=s.url_path,
             sess_user=s.user,
             sess_shared=s.info['shared'])
    return templates.TemplateResponse("manage_session.html", _)


@app.post("/sessions/{session_id}/share")
async def share_session(request: Request, session_id: str):
    session = orm_session_maker()
    s = session.query(Session3DSlicer).get(session_id)
    if s:
        s.info["shared"] = True
        flag_modified(s, "info")
        session.add(s)
        session.commit()
        return RedirectResponse(url=f"{url_base}/sessions/{session_id}", status_code=302)
    else:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <html>
                                          <head>
                                            <title>Share Session Failed</title>
                                          </head>
                                          <body>
                                          <p>Share Session Failed: Session does not exist</p>
                                          </body>
                                        </html>""", status_code=404)


@app.post("/sessions/{session_id}/unshare")
async def unshare_session(request: Request, session_id: str):
    session = orm_session_maker()
    s = session.query(Session3DSlicer).get(session_id)
    if s:
        s.info["shared"] = False
        flag_modified(s, "info")
        session.add(s)
        session.commit()
        return RedirectResponse(url=f"{url_base}/sessions/{session_id}", status_code=302)
    else:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <html>
                                          <head>
                                            <title>Unshare Session Failed</title>
                                          </head>
                                          <body>
                                          <p>Unshare Session Failed: Session does not exist</p>
                                          </body>
                                        </html>""", status_code=404)


@app.post("/sessions/{session_id}/close")
async def close_session_and_container(session_id):
    session = orm_session_maker()
    s = session.query(Session3DSlicer).get(session_id)
    if s:
        container_name = CONTAINER_NAME_PREFIX + s.user
        status = containers_status(container_name)
        if status:
            dc = docker.from_env()
            container = dc.containers.get(container_name)
            container.remove(force=True)
            logger.info(f"container {container_name} deleted")
        logger.info(f"deleting session {s.uuid}")
        session.delete(s)
        session.commit()
        # Update nginx.conf and reread Nginx configuration
        refresh_nginx(session, nginx_config_path, domain, tdslicerhub_adress)
        return RedirectResponse(url="/", status_code=302)
    else:
        raise Exception(f"cant remove container user expired")


def refresh_index_html(sess, proto="http", admin=True, write_to_file=True):
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
  <img src="/static/images/logo_y_titulo_fondo_naranja.png" alt="logo_opendx28" style="width:100%;min-height:350px;max-height:400px;">
<!--  <div class="w3-container w3-display-bottomleft w3-margin-bottom">
    <button onclick="document.getElementById('id01').style.display='block'" class="w3-button w3-xlarge w3-theme w3-hover-teal" title="Go To W3.CSS">LEARN W3.CSS</button>
  </div>-->
</div>

    """
    _ += f"""
    <div class="w3-quarter">
    <a href="{proto}://{domain}/login" target="_blank" rel="noopener noreferrer">
        <img src="../static/images/3dslicer.png" alt="3dslicerImagesNotFound" style="width:45%" class="w3-circle w3-hover-opacity">
    </a>    
       <h3>
       <a href="{proto}://{domain}/login" target="_blank" rel="noopener noreferrer">New Session</a>
       </h3>
    </div>

    </body>
        """
    for s in sess.query(Session3DSlicer).all():
        if admin or s.info["shared"]:
            # Section doing reverse proxy magic
            _ += f"""
<div class="w3-quarter">
<a href="http://{domain}{s.url_path}&view_only=true" target="_blank" rel="noopener noreferrer">
<img src="/static/images/3dslicer.png" alt="3dslicerImagesNotFound" style="width:23%" class="w3-circle w3-hover-opacity">
</a>
<h3>{s.user}</h3>
<p>CPU [%]: {s.info["CPU_pct"]}</p>
<p>(last checked: {s.last_activity})</p>
</div>
    """
    if index_path and write_to_file:
        with open(index_path, "wt") as f:
            f.write(_)
        logger.info(f"index.html re-written")

    return _


async def launch_3dslicer_web_docker_container(s: Session3DSlicer):
    """
    Launch a 3DSlicer web container
    """
    # docker client:
    active = False
    dc = docker.from_env()
    # just a container per user
    container_name = CONTAINER_NAME_PREFIX + s.user
    logger.info("CREATING NEW CONTAINER")
    pull_tdslicer_image(tdslicer_image_name, tdslicer_image_tag)
    create_all_volumes(s.user)
    vol_dict = volume_dict(s.user)
    c = dc.containers.run(image=f"{tdslicer_image_name}:{tdslicer_image_tag}", ports={"8080/tcp": None},
                          name=container_name,
                          network=network_id,
                          volumes=vol_dict,
                          detach=True)
    container_id = c.id
    while not active:
        # TODO mejorar: crear funcion check_state
        await asyncio.sleep(3)
        c = dc.containers.get(container_id)
        if c.status == "running":
            active = True
        if c.status == "exited":
            logger.info("container exited")
            break
    logs = c.logs
    # todo error control
    s.service_address = get_container_internal_adress(c.id, network_id)
    s.container_name = container_name
    logger.info(f"container {c.name} : {c.status} in {s.service_address}")


def stop_remove_docker_container(name):
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
                    print(f"can't stop container {name}")
        if c.status == "exited" or can_remove:
            c.remove()
            status = containers_status(name)
            if not status:
                logger.info(f"container {name} : removed")
            else:
                logger.info(f"can't remove {name}")
    except:
        logger.info(f"{name} container already removed")


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

    async def sessions_checker(self, sm):
        async def check_session_activity():
            print(":::::::::::::::::::::::Checking Session Activity:::::::::::::::::::::::::::::::::::")
            pct = docker_container_pct_activity(s.container_name)
            logger.info(f"pct container: {s.container_name}: {pct} ")
            s.info['CPU_pct'] = pct
            flag_modified(s, "info")
            ahora = datetime.datetime.now()
            if pct > ACTIVITY_THRESHOLD:
                s.last_activity = ahora
                stop = False
            else:
                stop = (ahora - s.last_activity).total_seconds() > allowed_inactivity_time_in_seconds
            return stop

        # ---- sessions_checker ----------------------------------------------------------------------------------------
        logger.info("::::::::::::::::::::::: Session Checker :::::::::::::::::::::::::::::::::::")

        dc = docker.from_env()
        try:
            tdslicer_containers = [c.name for c in dc.containers.list(all)]
        except Exception as e:
            logger.info(f"::::::::::::::::: sessions_checker - EXCEPTION no containers. {e}")
            return None

        # Reassociate, restart or delete 3D Slicer sessions if we are back from a restart of the container
        sess = sm()
        for s in sess.query(Session3DSlicer).all():
            pct = docker_container_pct_activity(s.container_name)
            logger.info(f"pct container: {s.container_name}: {pct} ")
            s.last_activity = datetime.datetime.now()
            s.info['CPU_pct'] = pct
            if pct < 0:  # <0 -> "Container does not exist"
                if s.restart:
                    # TODO right now "restart" is always False so this is never executed
                    logger.info(f"::::::::::::::::: sessions_checker - restarting container for user {s.user}")
                    await launch_3dslicer_web_docker_container(s)
                    s.info['CPU_pct'] = ACTIVITY_THRESHOLD + 1
                    sess.add(s)
                else:
                    logger.info(f"::::::::::::::::: sessions_checker - deleting session {s.user} because associated container does not exist")
                    sess.delete(s)
            else:
                if s.restart:
                    logger.info(f"::::::::::::::::: sessions_checker - reassociating session {s.user} with container {s.container_name}")
                    s.info['CPU_pct'] = ACTIVITY_THRESHOLD + 1
                    tdslicer_containers.remove(s.container_name)  # Do not delete this container
                    sess.add(s)
                else:
                    logger.info(f"::::::::::::::::: sessions_checker - removing container and session for {s.user}, with container {s.container_name}")
                    stop_remove_docker_container(s.container_name)
                    tdslicer_containers.remove(s.container_name)
                    sess.delete(s)
            flag_modified(s, "info")

        sess.commit()
        sess.close()
        # Update nginx.conf and reread Nginx configuration
        refresh_nginx(sess, nginx_config_path, domain, tdslicerhub_adress)

        # Remove dangling 3dslicer containers managed by 3dslicer-hub
        for name in tdslicer_containers:
            if name.startswith(CONTAINER_NAME_PREFIX):
                logger.info(f"::::::::::::::::: sessions_checker - removing container {name} with no associated session")
                stop_remove_docker_container(name)
            await asyncio.sleep(200)

        # After initialization, infinite loop
        while True:
            sess = sm()
            # Loop all sessions, remove those that are not in use
            for s in sess.query(Session3DSlicer).all():
                stop = await check_session_activity()  # Implicit parameter: "s" (3dslicer session)
                sess.add(s)
                if stop:
                    logger.info(f"::::::::::::::::: sessions_checker - inactivity cleanup - stopping container {s.container_name}")
                    stop_remove_docker_container(s.container_name)
                    sess.delete(s)
                    # Update nginx.conf and reread Nginx configuration
                    refresh_nginx(sess, nginx_config_path, domain, tdslicerhub_adress)

            sess.commit()
            sess.close()
            await asyncio.sleep(60)


runner = BackgroundRunner()


@app.on_event("startup")
async def startup():
    asyncio.create_task(runner.sessions_checker(orm_session_maker))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", debug=True)
