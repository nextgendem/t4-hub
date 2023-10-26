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
import re
import sys

from dotenv import load_dotenv

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import exc
from starlette.responses import RedirectResponse, HTMLResponse

import ldap3
from ldap3.core.exceptions import LDAPException
from tsliceh import create_session_factory, create_local_orm, Session3DSlicer, create_tables, get_ldap_address, \
    get_domain_name
from tsliceh.orchestrators import create_docker_network, IContainerOrchestrator, container_orchestrator_factory
from tsliceh.volumes import create_all_volumes, volume_dict
from tsliceh.helpers import get_container_internal_address
from fastapi.logger import logger
import logging.config
import logging

# INITIALIZE

app = FastAPI(root_path="")
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

env_file = os.getenv("ENV_FILE", None)
load_dotenv(env_file)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

# CONFIGURATION
db_conn_str = os.getenv("DB_CONNECTION_STRING")
ACTIVITY_THRESHOLD = 10  # Percentage of CPU usage to consider a container active
nginx_container_name = os.getenv('NGINX_NAME')  # Read from environment variable the name of the nginx container relative to this container
nginx_config_path = os.getenv('NGINX_CONFIG_FILE')  # Read from environment the location of nginx.conf for this container
index_path = os.getenv('INDEX_PATH')  # Path for the automatic index.html file
allowed_inactivity_time_in_seconds = int(os.getenv("INACTIVITY_TIME_SEC"))
network_name = os.getenv('NETWORK_NAME')
proto = os.getenv('PROTO')
nfs_server = os.getenv('NFS_SERVER')  # Not used. Teide provides NFS mounts directly to all nodes
ldap_base = "ou=jupyterhub,dc=opendx,dc=org"
co_str = os.getenv("CONTAINER_ORCHESTRATOR", default="kubernetes")
tdslicer_image_name = "localhost:5000/opendx28/slicer"
tdslicer_image_tag = "latest"
tdslicer_image_url = os.getenv("SLICER_IMAGE_DOCKERFILE", "https://github.com/OpenDx28/docker-slicer.git#:src")
base_vnc_image_name = "localhost:5000/vnc-base"
base_vnc_image_tag = "latest"
base_vnc_image_url = os.getenv("VNC_BASE_IMAGE_DOCKERFILE", "https://github.com/OpenDx28/docker-vnc-base.git#:src")
# END CONFIGURATION

domain = get_domain_name(os.getenv("MODE"), os.getenv('DOMAIN'), os.getenv('PORT', default=None))
url_base = f"{proto}://{domain}"
engine = create_local_orm(db_conn_str)
create_tables(engine)
orm_session_maker = create_session_factory(engine)

if co_str == "docker_compose":
    network_id = create_docker_network(network_name)
    ldap_address = get_ldap_address(os.getenv("MODE"), os.getenv("OPENLDAP_NAME"), network_id)
    CONTAINER_NAME_PREFIX = "h__tds__"

    # setup loggers https://github.com/tiangolo/uvicorn-gunicorn-fastapi-docker/issues/19#issuecomment-606672830
    logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"), disable_existing_loggers=False)
    gunicorn_logger = logging.getLogger('gunicorn.error')  # 1

    logger.handlers = gunicorn_logger.handlers
    if __name__ != "main":
        logger.setLevel(gunicorn_logger.level)
    else:
        logger.setLevel(logging.DEBUG)  # 2
elif co_str == "kubernetes":
    network_id = 0  # TODO Create network in kubernetes, obtain its id
    ldap_host = os.getenv("OPENLDAP_NAME")
    ldap_port = os.getenv("OPENLDAP_PORT")
    ldap_address = f"{ldap_host}:{ldap_port}"  # TODO Obtain ldap_adress from kubernetes
    CONTAINER_NAME_PREFIX = "slicer-"

    #logger = logging.getLogger(__name__)  # 1
    logger.setLevel(logging.DEBUG)  # 2
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.debug(f"===================\nLOGGER: {logger}\n=========================")

container_orchestrator = container_orchestrator_factory(co_str)
tdslicerhub_adress = get_container_internal_address(container_orchestrator, os.getenv("TDSLICERHUB_NAME"), network_id) \
    if os.getenv("MODE") != "local" else domain


async def refresh_nginx(co: IContainerOrchestrator, sess, nginx_cfg_path, domainn, tds_address):
    def generate_nginx_conf():
        """ For each session, generate a section, plus the first part """
        # "nginx.conf" prefix
        _ = f"""
user www-data;

events {{
}}

http {{
  log_format custom '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$uri" "$http_x_forwarded_for" "$request_filename"';
  server {{
    listen     80;
    server_name  {domainn};
    access_log /var/log/nginx/access2.log custom;
    error_log  /var/log/nginx/error2.log  debug;

    location / {{
      proxy_pass http://{tds_address};
    }}
    """
        # Variable length section, for each location
        if sess:
            for s in sess.query(Session3DSlicer).all():
                # Section doing reverse proxy magic
                _ += f"""
  
    location /{s.uuid}/ {{
        proxy_pass http://{s.service_address}/;          
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;           
    }}

    location /{s.uuid}-ws {{
        proxy_pass http://{s.service_address}/websockify;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        add_header Cache-Control no-cache;
    }}        


"""
        _ += f"""
  }}
}}
"""
        print(":::::::::::::::::::::::::::: CREATING NEW NGINX FILE :::::::::::::::::::::::::::::::::::::::::")
        print(_)
        if nginx_cfg_path:
            with open(nginx_cfg_path, "wt") as f:
                f.write(_)

    async def command_nginx_to_read_configuration(nginx_cont_name):
        """
        Given the name of the NGINX container used as reverse proxy for 3DSlicer sessions,
        command it to reread the configuration.
        """
        tries = 0
        while tries < 10:
            status = co.get_container_status(nginx_cont_name)
            logger.debug(f"NGINX status: {status}\n----------------")
            # TODO Needs better handling of statuses
            if status.lower() == "running":
                r = co.execute_cmd_in_nginx_container(nginx_cont_name, "/etc/init.d/nginx reload")
                if r is None:
                    co.start_base_containers()
                else:
                    return r
            else:
                await asyncio.sleep(2)
            tries += 1

    # -----------------------------------------------

    generate_nginx_conf()
    await command_nginx_to_read_configuration(nginx_container_name)


asyncio.run(refresh_nginx(container_orchestrator, None, nginx_config_path, domain, tdslicerhub_adress))
max_sessions = int(os.getenv("MAX_SESSIONS", default=1000))  # >= 1000 -> ignore
slicer_ini = os.getenv("SLICER_INI")


def count_active_session_containers(sess):
    # Obtain number of active sessions (with started container)
    cont = 0
    for s in sess.query(Session3DSlicer).all():
        pct = container_orchestrator.get_container_activity(s.container_name)
        if pct != -1:
            cont += 1
    return cont


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
        with ldap3.Connection(ldap_address, user=f"uid={user},{ldap_base}", password=password,
                              read_only=True) as conn:
            logger.info(conn.result["description"])  # "success" if bind is ok
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
    if re.match(r".*_gpu$", login_form.username):
        gpu = True
    else:
        gpu= False
    if await check_credentials(username, password):
        if await can_open_session(username):
            session = orm_session_maker()
            s = Session3DSlicer()
            container_launched = False
            try:
                s = session.query(Session3DSlicer).filter(Session3DSlicer.user == username).first()
                if not s:
                    # Create new session (IF there is room)
                    cont = count_active_session_containers(session)
                    if cont < max_sessions:
                        s.user = username
                        s.last_activity = datetime.datetime.now()
                        s.gpu = gpu
                        session.add(s)
                        session.flush()
                        s.url_path = f"/{s.uuid}/"
                        # Launch new 3d slicer container (it also sets the "container_name" field)
                        await launch_3dslicer_web_container(s)
                        container_launched = True
                        pct = container_orchestrator.get_container_activity(s.container_name)
                        s.info = {'CPU_pct': pct, 'shared': False}
                        # Commit new
                        session.add(s)
                        session.commit()
                        # Update nginx.conf and reread Nginx configuration
                        await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, tdslicerhub_adress)
                    else:
                        return HTMLResponse(content=f"""<!DOCTYPE html>
                                                        <html>
                                                          <head>
                                                            <title>Max number of sessions reached</title>
                                                          </head>
                                                          <body>
                                                          <p>Cannot open a new session, {max_sessions} reached. Please close other sessions</p>
                                                          </body>
                                                        </html>""", status_code=401)
            except exc.SQLAlchemyError as e:
                if container_launched:
                    stop_remove_container(s.container_name)
                session.rollback()
                raise e
            finally:
                session.close()

            # Redirect to a session management page:
            return RedirectResponse(url=f"/sessions/{s.uuid}", status_code=302)
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
    session.close()
    return templates.TemplateResponse("manage_session.html", _)


@app.post("/sessions/{session_id}/share")
async def share_session(request: Request, session_id: str, interactive: int = 0):
    session = orm_session_maker()
    s = session.query(Session3DSlicer).get(session_id)
    if s:
        s.info["shared"] = True
        s.info["shared_interactive"] = interactive
        flag_modified(s, "info")
        session.add(s)
        session.commit()
        session.close()
        return RedirectResponse(url=f"/sessions/{session_id}", status_code=302)
    else:
        session.close()
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
        session.close()
        return RedirectResponse(url=f"/sessions/{session_id}", status_code=302)
    else:
        session.close()
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
        container_name = CONTAINER_NAME_PREFIX + container_orchestrator.get_valid_name(s.user)
        status = container_orchestrator.get_container_status(container_name)
        if status:
            stop_remove_container(container_name, True)
            logger.info(f"container {container_name} deleted")
        logger.info(f"deleting session {s.uuid}")
        session.delete(s)
        session.commit()
        # Update nginx.conf and reread Nginx configuration
        await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, tdslicerhub_adress)
        session.close()
        return RedirectResponse(url="/", status_code=302)
    else:
        session.close()
        raise Exception(f"cant remove container user expired")


def refresh_index_html(sess, proto="http", admin=True, write_to_file=True):

    if max_sessions < 1000:
        cont = count_active_session_containers(sess)
        sessions_cont = f"({cont}/{max_sessions})"
    else:
        sessions_cont = ""

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
    <a href="/login" target="_blank" rel="noopener noreferrer">
        <img src="../static/images/3dslicer.png" alt="3dslicerImagesNotFound" style="width:45%" class="w3-circle w3-hover-opacity">
    </a>    
       <h3>
       <a href="/login" target="_blank" rel="noopener noreferrer">New (or reconnect to) Session {sessions_cont}</a>
       </h3>
    </div>

    </body>
        """
    for s in sess.query(Session3DSlicer).all():
        if admin or s.info["shared"]:
            # Section doing reverse proxy magic
            _ += f"""
<div class="w3-quarter">
<a href="{s.url_path}" target="_blank" rel="noopener noreferrer">
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


async def launch_3dslicer_web_container(s: Session3DSlicer):
    """
    Launch a 3DSlicer web container
    """
    # just a container per user
    container_name = CONTAINER_NAME_PREFIX + container_orchestrator.get_valid_name(s.user)

    logger.info("CREATING NEW CONTAINER")
    container_orchestrator.create_image(tdslicer_image_name, tdslicer_image_tag)
    create_all_volumes(container_orchestrator, s.user)
    vol_dict = volume_dict(s.user)
    c = await container_orchestrator.start_container(container_name, tdslicer_image_name, tdslicer_image_tag,
                                                     network_id, vol_dict, s.uuid, use_gpu = s.gpu)
    logs = c.logs
    # todo error control
    s.service_address = get_container_internal_address(container_orchestrator, c.id, network_id)
    s.container_name = container_name
    logger.info(f"container {c.name} : {c.status} in {s.service_address}")


def stop_remove_container(name, force_remove=False):
    """
    in certain session, stop container when:
    - session expires
    - order from user
    - order from administrator
    :param name:
    :return:
    """
    # TODO MANAGE THOSE PRINTS
    stopped = container_orchestrator.stop_container(name)
    if stopped is True:
        removed = container_orchestrator.remove_container(name)
        if removed:
            logger.info(f"container {name} : removed")
        elif removed is False:
            logger.info(f"can't remove {name}")
        else:
            logger.info(f"container {name} : does not exist")


@app.api_route("/{path_name:path}", methods=["GET"])
def catch_all(path_name: str, request: Request):
    logger.debug(f"Unknown path: {path_name}")
    logger.debug(f"Request: {request.url}")
    return HTMLResponse(content=f"""<!DOCTYPE html>
                                    <html>
                                      <head>
                                        <title>Unknown path</title>
                                      </head>
                                      <body>
                                      <p>Path: {path_name} not supported</p>
                                      </body>
                                    </html>""", status_code=200)


class BackgroundRunner:
    def __init__(self):
        self.session_maker = None

    async def sessions_checker(self, sm):
        async def check_session_activity(s):
            print(":::::::::::::::::::::::Checking Session Activity:::::::::::::::::::::::::::::::::::")
            pct = container_orchestrator.get_container_activity(s.container_name)
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

        tdslicer_containers = container_orchestrator.get_tdscontainers(CONTAINER_NAME_PREFIX)

        # Reassociate, restart or delete 3D Slicer sessions if we are back from a restart of the container
        sess = sm()
        for s in sess.query(Session3DSlicer).all():
            pct = container_orchestrator.get_container_activity(s.container_name)
            logger.info(f"pct container: {s.container_name}: {pct} ")
            s.last_activity = datetime.datetime.now()
            s.info['CPU_pct'] = pct
            if pct < 0:  # <0 -> "Container does not exist"
                if s.restart:
                    # TODO right now "restart" is always False so this is never executed
                    logger.info(f"::::::::::::::::: sessions_checker - restarting container for user {s.user}")
                    await launch_3dslicer_web_container(s)
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
                    stop_remove_container(s.container_name)
                    tdslicer_containers.remove(s.container_name)
                    sess.delete(s)
            flag_modified(s, "info")

        sess.commit()
        sess.close()
        # Update nginx.conf and reread Nginx configuration
        await refresh_nginx(container_orchestrator, sess, nginx_config_path, domain, tdslicerhub_adress)

        # Remove dangling 3dslicer containers managed by 3dslicer-hub
        for name in tdslicer_containers:
            if name.startswith(CONTAINER_NAME_PREFIX):
                logger.info(f"::::::::::::::::: sessions_checker - removing container {name} with no associated session")
                stop_remove_container(name)

        # After initialization, infinite loop
        while True:
            sess = sm()
            # Loop all sessions, remove those that are not in use
            for s in sess.query(Session3DSlicer).all():
                print(f"Session - Name: {s.container_name};\n UUID: {s.uuid};\n User: {s.user}\n")
                stop = await check_session_activity(s)  # Implicit parameter: "s" (3dslicer session)
                sess.add(s)
                if stop:
                    logger.info(f"::::::::::::::::: sessions_checker - inactivity cleanup - stopping container {s.container_name}")
                    stop_remove_container(s.container_name)
                    sess.delete(s)
                    # Update nginx.conf and reread Nginx configuration
                    await refresh_nginx(container_orchestrator, sess, nginx_config_path, domain, tdslicerhub_adress)

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
