"""
3DSlicer Hub aims to imitate the functionality of JupyterHub, but for 3DSlicer:
  - Provide a login mechanism and a login page
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
import uuid

from dotenv import load_dotenv

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import exc
from starlette.responses import RedirectResponse, HTMLResponse

from apphub import create_session_factory, create_local_orm, AppSession, create_tables, \
    get_domain_name
from apphub.gunicorn_config import lock
from contextlib import nullcontext
from apphub.orchestrators import create_docker_network, IContainerOrchestrator, container_orchestrator_factory
from apphub.volumes import create_all_volumes, volume_dict
from apphub.helpers import get_container_internal_address
from fastapi.logger import logger
import logging.config
import logging

from fastapi.security import OAuth2PasswordBearer
import requests

from requests.exceptions import RequestException

from jose import jwt





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

# TODO Revisit, different k8s clusters will have different persistent volumes
nfs_server = os.getenv('NFS_SERVER')  # Not used. Teide provides NFS mounts directly to all nodes

co_str = os.getenv("CONTAINER_ORCHESTRATOR", default="kubernetes")
app_image_name = "transformer4"
app_image_tag = "latest"
app_image_url = os.getenv("APP_IMAGE_DOCKERFILE", "https://github.com/nextgendem/t4-novnc#:src")
base_vnc_image_name = "vnc-base"
base_vnc_image_tag = "latest"
base_vnc_image_url = os.getenv("VNC_BASE_IMAGE_DOCKERFILE", "https://github.com/OpenDx28/docker-vnc-base.git#:src")
# END CONFIGURATION

domain = get_domain_name(os.getenv("MODE"), os.getenv('DOMAIN'), os.getenv('PORT', default=None))
url_base = f"{proto}://{domain}"
engine = create_local_orm(db_conn_str)
create_tables(engine)
orm_session_maker = create_session_factory(engine)
db_access_lock = nullcontext() if "postgresql" in db_conn_str else lock

if co_str == "docker_compose":
    network_id = create_docker_network(network_name)
    CONTAINER_NAME_PREFIX = "h__app__"

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
    CONTAINER_NAME_PREFIX = os.getenv("CONTAINER_NAME_PREFIX", "app-")

    # logger = logging.getLogger(__name__)  # 1
    logger.setLevel(logging.DEBUG)  # 2
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.debug(f"===================\nLOGGER: {logger}\n=========================")

container_orchestrator = container_orchestrator_factory(co_str)
app_hub_address = get_container_internal_address(container_orchestrator, os.getenv("APP_HUB_NAME"), network_id) \
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
  sub_filter_types text/html text/css application/javascript;
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
      proxy_connect_timeout 300s;
      proxy_read_timeout 600s;      
    }}
    """
        # Variable length section, for each location
        if sess:
            for s in sess.query(AppSession).all():
                # Section doing reverse proxy magic
                _ += f"""

    location /{s.uuid}/ {{
        proxy_pass http://{s.service_address}/;          
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;    
        proxy_http_version 1.1;        
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        add_header Cache-Control no-cache;        
    }}
    
    location /{s.uuid}-files/ {{
        proxy_pass http://{s.other_address}/;
        sub_filter 'href="/'  'href="/{s.uuid}-files/';
        sub_filter 'src="/'  'src="/{s.uuid}-files/';
        sub_filter_once off;

        proxy_set_header Host $host;
        client_max_body_size 100M;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme; 
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
            print(nginx_cont_name)
            logger.debug(f"NGINX status: {status}\n----------------")
            # TODO Needs better handling of statuses
            if status.lower() == "running":
                r = co.execute_cmd_in_nginx_container(nginx_cont_name, "nginx -s reload")
                if r is None:
                    co.start_base_containers()
                else:
                    print(r)
                    return r
            else:
                await asyncio.sleep(2)
            tries += 1

    # -----------------------------------------------

    generate_nginx_conf()
    await command_nginx_to_read_configuration(nginx_container_name)


asyncio.run(refresh_nginx(container_orchestrator, None, nginx_config_path, domain, app_hub_address))
max_sessions = int(os.getenv("MAX_SESSIONS", default=1000))  # >= 1000 -> ignore


def count_active_session_containers(sess):
    # Obtain number of active sessions (with started container)
    cont = 0
    for s in sess.query(AppSession).all():
        pct = container_orchestrator.get_container_activity(s.container_name)
        if pct != -1:
            cont += 1
    return cont


# Welcome & login page
@app.get("/index.html")
async def index_page(id : str = "0"):
    with db_access_lock:
        session = orm_session_maker()
        r = HTMLResponse(content=refresh_index_html(session,id, proto=proto, admin=False, write_to_file=False),
                            status_code=200)
        session.close()
        return r


@app.get("/")
async def welcome_and_login_page(request: Request):
    # Jinja2 template with login page
    _ = dict(request=request)
    return templates.TemplateResponse("login.html", _)


async def check_credentials(user, password):
    return True


async def can_open_session(user):
    return True  # TODO Authentication

# Replace these with your own values from the Google Developer Console
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Authentication with Google, redirects to /oauth2/callback
# Redirect Uri needs to be allowed in Google Cloud Platform

@app.post("/login/google")
async def login_google():
    google_auth_url = (
        f"https://accounts.google.com/o/oauth2/auth?"
        f"response_type=code&"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={GOOGLE_REDIRECT_URI}&"
        f"scope=openid%20profile%20email&"
        f"access_type=offline"
    )
    return RedirectResponse(url=google_auth_url)

# get users from internal server
# important to get roles of main platform (such as transformer4-guest, sys-admin,...)
# main parameter: email

def get_user_roles(email, protocol_server=None):
    if not protocol_server:
        protocol_server = os.environ.get("NGD_PROTOCOL_SERVER", "https://sys.nextgendem.eu")
    try:
        response = requests.get(
            f"{protocol_server}/api/user_roles",
            params={'email': email},
            headers={'Cache-Control': 'no-cache'}
        )
        response.raise_for_status()
    except RequestException as e:
        print(f"A network error occurred: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"An HTTP error occurred: {e}")
        return []
    else:
        return response.json()["roles"]
        
# Main verification of user via Google Authentication
# returns user_info[] (["email"],["username"],["verified_email"])
# Also creates the session (ID: Google User ID) if it's autenthicated
# Redirects to other function with the HTML responses
        
@app.get("/oauth2/callback")
async def auth_google(code: str,request: Request):
    token_url = "https://accounts.google.com/o/oauth2/token"
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(token_url, data=data)
    access_token = response.json().get("access_token")
    request_info = requests.get("https://www.googleapis.com/oauth2/v1/userinfo", headers={"Authorization": f"Bearer {access_token}"})
    
    user_info = request_info.json()
    user_email = user_info["email"]
    if not user_info["verified_email"]:
        return HTMLResponse(content="""<!DOCTYPE html>
                                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
                                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                                            <link href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300..800;1,300..800&display=swap" rel="stylesheet">  
                                            <style>
                                                p {
                                                    font-family: "Open Sans", serif;
                                                    font-family: "Open Sans", serif;
                                                      font-optical-sizing: auto;
                                                      font-weight: <weight>;
                                                      font-style: normal;
                                                      font-variation-settings:
                                                        "wdth" 100;                                                }
                                            </style>
                                    <body id="myPage">
                                    <!-- Image Header -->
                                    <header class="p-2 text-bg-dark">
                                        <div class="container">
                                          <div class="d-flex flex-wrap align-items-center justify-content-center justify-content-lg-start">
                                            <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
                                              <img class="me-3" src="/static/images/LogoNEXTGENDEM_Color_cropped.png" alt="logo_nextgem" width="40">
                                            </a>
                                            <span class="me-5 me-lg-auto fs-4 font-weight-bold" style="color:#FFFFFF;font-weight: 500;">NEXTGENDEM</span>
                                            <div class="text-end">
                                              <a href="https://demiurge.nextgendem.eu/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
                                              <img class="me-3" src="/static/images/logo_demiurge.png" alt="logo_nextgem" width="40">
                                              </a>
                                            </div>
                                          </div>
                                        </div>
                                      </header>
                                    <html>
                                      <head>
                                        <title>Login Failed</title>
                                      </head>
                                        <body>
                                          <p>Login Failed: Email not verified</p>
                                        </body>
                                      </html>""", status_code=401)
            
    user_roles = get_user_roles(user_email)
    is_authorized = "transformer4-admin" in user_roles or "sys-admin" in user_roles

    is_guest = "transformer4-guest" in user_roles
    if is_guest:
      _ = dict(request=request)
      return RedirectResponse(url=f"/index.html", status_code=302)
    
    if not is_authorized:
            return HTMLResponse(content="""<!DOCTYPE html>
                                        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
                                        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                                            <link href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300..800;1,300..800&display=swap" rel="stylesheet">  
                                            <style>
                                                header {
                                                    z-index: 10
                                                }
                                                p {
                                                    animation-duration: 3s;
                                                    animation-name: slidein;
                                                    font-family: "Open Sans", serif;
                                                      font-optical-sizing: auto;
                                                      font-weight: bold;
                                                      font-style: normal;
                                                      font-variation-settings:
                                                        "wdth" 100;
                                                    text-align: center;
                                                    font-size: 20px;
                                                    padding-top: 200px;
                                                }
                                                
                                                use{
                                                  animation:move-forever 2s linear infinite;
                                                  &:nth-child(2){ animation-duration:2.5s; animation-delay:-1.5s; }
                                                  &:nth-child(1){ animation-duration:5s}
                                                }

                                                @keyframes move-forever{
                                                   0%{transform: translate(-2px , 0)}
                                                 100%{transform: translate( 0px , 0)} 
                                                }


                                                /* layout only*/
                                                svg{
                                                z-index: -10;
                                                bottom: 0;
                                                left: 0;
                                                position: fixed;
                                                width: 100%;
                                                }
                                                
                                                @keyframes slidein {
                                                  from {
                                                    transform: translate(0,-150%);
                                                  }

                                                  to {
                                                    transform: translate(0,0%);
                                                  }
                                                }
                                                @keyframes transform {
                                                  from {
                                                    height: 200%;
                                                  }

                                                  to {
                                                    height: 50%;
                                                  }
                                                }
                                            </style>
                                        <body id="myPage">
                                        <!-- Image Header -->
                                        <header class="p-2 text-bg-dark">                                 
                                            <div class="container">
                                              <div class="d-flex flex-wrap align-items-center justify-content-center justify-content-lg-start">
                                                <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
                                                  <img class="me-3" src="/static/images/LogoNEXTGENDEM_Color_cropped.png" alt="logo_nextgem" width="40">
                                                </a>
                                                <span class="me-5 me-lg-auto fs-4 font-weight-bold" style="color:#FFFFFF;font-weight: 500;">NEXTGENDEM</span>
                                                <div class="text-end">
                                                  <a href="https://demiurge.nextgendem.eu/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
                                                  <img class="me-3" src="/static/images/logo_demiurge.png" alt="logo_nextgem" width="40">
                                                  </a>
                                                </div>
                                              </div>
                                            </div>
                                          </header>
                                        <html>
                                          <head>
                                            <title>Login Failed</title>
                                          </head>
                                          <body>
                                          <p>Login Failed: Unauthorized</p>
                                          </body>
                                          <svg 
                                             viewBox="0 0 2 1" 
                                             preserveAspectRatio="none">
                                              <defs>
                                                <path id="w" 
                                                  d="
                                                  m0 1v-.5 
                                                  q.5.5 1 0
                                                  t1 0 1 0 1 0
                                                  v.5z" />
                                              </defs>
                                              <g>
                                               <use href="#w" y=".0" fill="#2d55aa" />
                                               <use href="#w" y=".1" fill="#2B4375" />
                                               <use href="#w" y=".2" fill="#26344F" />
                                              </g>
                                             </svg>
                                        </html>""", status_code=401)
            
    username = user_info["id"]
    if re.match(r".*_gpu$", user_info["id"]):
        gpu = True
    else:
        gpu = False
    if await can_open_session(username):
        with db_access_lock:
            session = orm_session_maker()
            container_launched = False
            try:
                s = session.query(AppSession).filter(AppSession.user == username).first()
                if not s:
                    # Create new session (IF there is room)
                    cont = count_active_session_containers(session)
                    if cont < max_sessions:
                        s = AppSession()
                        s.uuid = uuid.uuid4()
                        s.user = username
                        s.email = user_info["email"]
                        s.last_activity = datetime.datetime.now()
                        s.gpu = gpu
                        s.url_path = f"/{s.uuid}/"
                        # Launch new 3d slicer container (it also sets the "container_name" field)
                        await launch_app_web_container(s)
                        container_launched = True
                        pct = container_orchestrator.get_container_activity(s.container_name)
                        s.info = {'CPU_pct': pct, 'shared': False}
                        # Commit new
                        session.add(s)
                        session.commit()
                        # Update nginx.conf and reread Nginx configuration
                        await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, app_hub_address)
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
            response = RedirectResponse(url=f"/sessions/{s.uuid}?page=main", status_code=302)
            response.set_cookie(key="source", value=s.uuid)
            return response
    else:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.googleapis.com">
                                            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                                            <link href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300..800;1,300..800&display=swap" rel="stylesheet">  
                                            <style>
                                                p {
                                                    font-family: "Open Sans", serif;
                                                    font-family: "Open Sans", serif;
                                                      font-optical-sizing: auto;
                                                      font-weight: <weight>;
                                                      font-style: normal;
                                                      font-variation-settings:
                                                        "wdth" 100;                                                }
                                            </style>
                                        <body id="myPage">
                                        <!-- Image Header -->
                                        <header class="p-2 text-bg-dark">
                                            <div class="container">
                                              <div class="d-flex flex-wrap align-items-center justify-content-center justify-content-lg-start">
                                                <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
                                                  <img class="me-3" src="/static/images/LogoNEXTGENDEM_Color_cropped.png" alt="logo_nextgem" width="40">
                                                </a>
                                                <span class="me-5 me-lg-auto fs-4 font-weight-bold" style="color:#FFFFFF;font-weight: 500;">NEXTGENDEM</span>
                                                <div class="text-end">
                                                  <a href="https://demiurge.nextgendem.eu/" class="d-block link-body-emphasis text-decoration-none" data-bs-toggle="dropdown" aria-expanded="true">
                                                    <img src="/static/images/logo_demiurge.png" alt="mdo" width="32" height="32" class="rounded-circle">
                                                  </a>
                                                </div>
                                              </div>
                                            </div>
                                          </header>
                                        <html>
                                          <head>
                                            <title>Login Failed</title>
                                          </head>
                                          <body>
                                          <p>Login Failed: Your user ID or password is incorrect</p>
                                          </body>
                                        </html>""", status_code=401)
    
# Start (or resume) 3DSlicer session
# OBSOLETE: Login via form
# ID = username

@app.post("/login")
async def login(login_form: OAuth2PasswordRequestForm = Depends()):
    username = login_form.username
    password = login_form.password
    if re.match(r".*_gpu$", login_form.username):
        gpu = True
    else:
        gpu = False
    if await check_credentials(username, password):
        if await can_open_session(username):
            with db_access_lock:
                session = orm_session_maker()
                container_launched = False
                session_uuid = None
                try:
                    s = session.query(AppSession).filter(AppSession.user == username).first()
                    if not s:
                        # Create new session (IF there is room)
                        cont = count_active_session_containers(session)
                        if cont < max_sessions:
                            s = AppSession()
                            s.uuid = uuid.uuid4()
                            s.user = username
                            s.email = "none"
                            s.last_activity = datetime.datetime.now()
                            s.gpu = gpu
                            s.url_path = f"/{s.uuid}/"
                            # Launch new 3d slicer container (it also sets the "container_name" field)
                            await launch_app_web_container(s)
                            container_launched = True
                            pct = container_orchestrator.get_container_activity(s.container_name)
                            s.info = {'CPU_pct': pct, 'shared': False}
                            # Commit new
                            session.add(s)
                            session.commit()
                            # Update nginx.conf and reread Nginx configuration
                            await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, app_hub_address)
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
                    # Save UUID before closing session
                    session_uuid = s.uuid
                except exc.SQLAlchemyError as e:
                    if container_launched:
                        stop_remove_container(s.container_name)
                    session.rollback()
                    raise e
                finally:
                    session.close()

            response = RedirectResponse(url=f"/sessions/{session_uuid}?page=main", status_code=302)
            response.set_cookie(key="source", value=session_uuid)
            return response
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

# HTML Responses main hub
# View of the user's session and other sessions
# Works only when refreshing, not possible (nor recreated) in app.get

@app.post("/sessions/{session_id}")
async def get_session_management_page(request: Request, session_id: str, page):
    source = request.cookies.get("source", "unknown")
    if source != session_id:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <html>
                                          <head>
                                            <title>Login Failed</title>
                                          </head>
                                          <body>
                                          <p>Access not authorized</p>
                                          </body>
                                        </html>""", status_code=401)
                                        
    session = orm_session_maker()
    s = session.query(AppSession).get(session_id)
    lst = []
    if s is None:
        _ = dict(request=request,
                 url_base="",
                 sessions_list=lst,
                 sess_uuid=session_id,
                 sess_link=f"",
                 files_link=f"",
                 sess_email="Not email found",
                 sess_user="Session ID not found",
                 sess_shared="Session ID not found")
    else:
        user_rol = get_user_roles(s.email)
        # check if it's admin or not
        is_admin = "sys-admin" in user_rol
        if is_admin:
            for _ in session.query(AppSession).all():
                d = {c.name: getattr(_, c.name) for c in _.__table__.columns}
                lst.append(d)

        _ = dict(request=request,
                 url_base="",
                 sessions_list=lst,
                 sess_uuid=session_id,
                 sess_link=s.url_path,
                 files_link=f"/{s.uuid}-files/",
                 sess_user=s.user,
                 sess_email=s.email,
                 sess_shared=s.info['shared'])
    # n = 0
    # while True:
    #     container_status = container_orchestrator.get_container_status(s.container_name)
    #     if container_status == "Status: Running":
    #         break
    #     if n == 10:
    #         container_status = "can't initiate 3dSlicer"
    #     await time.sleep(1)
    #     n = + 1

    with db_access_lock:
        session = orm_session_maker()
        r = HTMLResponse(content=refresh_manage_session_html(lst,session_id, session,page, proto=proto, admin=False, write_to_file=False),
                            status_code=200)
        print("Respuesta:" + str(r))
        session.close()
        return r

# HTML Responses main hub
# View of the user's session and other sessions

@app.get("/sessions/{session_id}")
async def get_session_management_page(request: Request, session_id: str, page):
    
    source = request.cookies.get("source", "unknown")
    if source != session_id:
        return HTMLResponse(content="""<!DOCTYPE html>
                                        <html>
                                          <head>
                                            <title>Login Failed</title>
                                          </head>
                                          <body>
                                          <p>Access not authorized</p>
                                          </body>
                                        </html>""", status_code=401)
                                        
    session = orm_session_maker()
    s = session.query(AppSession).get(session_id)
    lst = []
    if s is None:
        _ = dict(request=request,
                 url_base="",
                 sessions_list=lst,
                 sess_uuid=session_id,
                 sess_link=f"",
                 files_link=f"",
                 sess_email="Not email found",
                 sess_user="Session ID not found",
                 sess_shared="Session ID not found")
    else:
        user_rol = get_user_roles(s.email)
        # check if it's admin or not
        is_admin = "sys-admin" in user_rol
        if is_admin:
            for _ in session.query(AppSession).all():
                d = {c.name: getattr(_, c.name) for c in _.__table__.columns}
                lst.append(d)

        _ = dict(request=request,
                 url_base="",
                 sessions_list=lst,
                 sess_uuid=session_id,
                 sess_link=s.url_path,
                 files_link=f"/{s.uuid}-files/",
                 sess_user=s.user,
                 sess_email=s.email,
                 sess_shared=s.info['shared'])
    # n = 0
    # while True:
    #     container_status = container_orchestrator.get_container_status(s.container_name)
    #     if container_status == "Status: Running":
    #         break
    #     if n == 10:
    #         container_status = "can't initiate 3dSlicer"
    #     await time.sleep(1)
    #     n = + 1

    with db_access_lock:
        session = orm_session_maker()
        r = HTMLResponse(content=refresh_manage_session_html(lst,session_id, session,page, proto=proto, admin=False, write_to_file=False),
                            status_code=200)
        print("Respuesta:" + str(r))
        session.close()
        return r
    #return templates.TemplateResponse("manage_session.html", _)

# NOT USED
# Delete session from this database (only sys-admin)

@app.post("/sessions/{admin_id}/{session_id}/delete")
async def close_session_and_container(admin_id,session_id):
    with db_access_lock:
        session = orm_session_maker()
        s = session.query(AppSession).get(session_id)
        if s:
            container_name = CONTAINER_NAME_PREFIX + container_orchestrator.get_valid_name(s.user)
            status = container_orchestrator.get_container_status(container_name)
            if status:
                stop_remove_container(container_name, True)
                logger.info(f"container {container_name} deleted")
            logger.info(f"deleting session {s.uuid}")
            session.delete(s)
            session.commit()
            #Update nginx.conf and reread Nginx configuration
            await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, app_hub_address)
            session.close()
            if admin_id == session_id:
                return RedirectResponse(url="/", status_code=302)
            return RedirectResponse(url=f"/sessions/{admin_id}?page=main", status_code=302)
        else:
            session.close()
            raise Exception(f"cant remove container user expired")

# Share button
# Has variable ?interactive= (0 for only view, 1 for interact)
# Makes de session visible to users with transformer4-admin / sys-admin roles

@app.post("/sessions/{session_id}/share")
async def share_session(request: Request, session_id: str, interactive: int = 0):
    with db_access_lock:
        session = orm_session_maker()
        s = session.query(AppSession).get(session_id)
        if s:
            s.info["shared"] = True
            s.info["shared_interactive"] = interactive
            flag_modified(s, "info")
            session.add(s)
            session.commit()
            session.close()
            return RedirectResponse(url=f"/sessions/{session_id}?page=main", status_code=302)
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

# Unshare button
# Removes session visibility from other users

@app.post("/sessions/{session_id}/unshare")
async def unshare_session(request: Request, session_id: str):
    with db_access_lock:
        session = orm_session_maker()
        s = session.query(AppSession).get(session_id)
        if s:
            s.info["shared"] = False
            flag_modified(s, "info")
            session.add(s)
            session.commit()
            session.close()
            return RedirectResponse(url=f"/sessions/{session_id}?page=main", status_code=302)
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

# Deletes user sessions (via same user, or super admin)

@app.post("/sessions/{session_id}/close")
async def close_session_and_container(session_id):
    with db_access_lock:
        session = orm_session_maker()
        s = session.query(AppSession).get(session_id)
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
            await refresh_nginx(container_orchestrator, session, nginx_config_path, domain, app_hub_address)
            session.close()
            return RedirectResponse(url="/", status_code=302)
        else:
            session.close()
            raise Exception(f"cant remove container user expired")

# HTML of session
# Main page, redirected from Google Auth HTML 
# Views of the user management session page and see available session page
# Visibilty changes from different roles
# transfomer4-admin (admin) -> Manage/See sessions
# sys-admin (super-admin) -> + (from manage session) see session properties of other sessions and can delete sessions

def refresh_index_html(sess,id, proto="http", admin=True, write_to_file=True):

    if max_sessions < 1000:
        cont = count_active_session_containers(sess)
        sessions_cont = f"({cont}/{max_sessions})"
    else:
        sessions_cont = ""

    _ = """
<!DOCTYPE html>
<html>
<head>
<title>T4-Hub Sessions:</title>
<meta charset="UTF-8">
<script src="https://www.w3schools.com/lib/w3.js"></script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://www.w3schools.com/lib/w3-theme-black.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
</head>
<body id="myPage">
<!-- Image Header -->
<header class="p-2 text-bg-dark">
    <div class="container">
      <div class="d-flex flex-wrap align-items-center justify-content-center justify-content-lg-start">
        <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
          <img class="me-3" src="/static/images/LogoNEXTGENDEM_Color_cropped.png" alt="logo_nextgem" width="40">
        </a>
        <span class="me-5 me-lg-auto fs-4 font-weight-bold" style="color:#FFFFFF;font-weight: 500;">NEXTGENDEM</span>
        """
    if id != "0":
        _ += f"""
                <select id="rolViewChange" class="form-select mr-2" style="max-width: 15vh" onchange="this.options[this.selectedIndex].value && (window.location = this.options[this.selectedIndex].value);">
                    <option value="/sessions/{id}?page=main">Admin</option>
                    <option selected value="/index.html?id={id}">Guest</option>
                </select>
            """

    _ += """
        <div class="text-end">
          <a href="https://demiurge.nextgendem.eu/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
            <img class="me-3" src="/static/images/logo_demiurge.png" alt="logo_nextgem" width="40">
          </a>
        </div>
      </div>
    </div>
  </header>
<main class="d-flex flex-nowrap">
<div class="d-flex flex-column flex-shrink-0 p-3 text-bg-dark" style="width: 250px; min-height: 100vh; max-height: auto">
    <ul class="nav nav-pills flex-column mb-auto">
      <li id="availableSelector">
        <button id="buttonAvailable" href="#" class="nav-link text-white active" onclick="hideCreate()">
          <svg class="bi pe-none me-2" width="16" height="16"><use xlink:href="#speedometer2"></use></svg>
          Available sessions
        </button>
      </li>
  </div>
  
  <div class="w3-display-container w3-animate-opacity">
    <!--  <div class="w3-container w3-display-bottomleft w3-margin-bottom">
        <button onclick="document.getElementById('id01').style.display='block'" class="w3-button w3-xlarge w3-theme w3-hover-teal" title="Go To W3.CSS">LEARN W3.CSS</button>
      </div>-->
    </div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
    """
    for s in sess.query(AppSession).all():
            if admin or s.info["shared"]:
                # Section doing reverse proxy magic
                if s.info.get('shared_interactive', 0):
                    _url = s.url_path
                else:
                    _url = f"{s.url_path}/?view_only=true"
                _ += f"""
    <div id="displaySessions" class="p-3 w3-quarter">
    <a href="{_url}" target="_blank" rel="noopener noreferrer">
    <img src="/static/images/transformer4.png" alt="transformer4image" style="width:23%" class="w3-circle w3-hover-opacity">
    </a>
    <h3>{s.user}</h3>
    <p>CPU [%]: {s.info["CPU_pct"]}</p>
    <p>(last checked: {s.last_activity})</p>
    </div>
    </main>
    """
    
    if index_path and write_to_file:
        with open(index_path, "wt") as f:
            f.write(_)
        logger.info(f"index.html re-written")

    return _

def refresh_manage_session_html(lst,sess_uuid,sess,page, proto="http", admin=True, write_to_file=True):
    s_local = sess.query(AppSession).get(sess_uuid)
    if not s_local:
        _ = f"""
        <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
          Sesión desconectada
        </a>
        """
        return _
    user_rol = get_user_roles(s_local.email)
    if max_sessions < 1000:
        cont = count_active_session_containers(sess)
        sessions_cont = f"({cont}/{max_sessions})"
    else:
        sessions_cont = ""

    _ = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="50">
    <title>Login</title>
    <script src="https://www.w3schools.com/lib/w3.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/twinklecss@1.1.0/twinkle.min.css"/>
	<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <script>
        function openUrl(url, title) {{
            if (!title) {{
                title = 'Just another window';
            }}
            let x = window.open(url, title, 'toolbar=1,location=1,directories=1,status=1,menubar=1,scrollbars=1,resizable=1');
            x.blur();
            x.close();
            window.open(url, title, 'toolbar=1,location=1,directories=1,status=1,menubar=1,scrollbars=1,resizable=1');
        }}
    </script>
    <style>
        table {{
            border-collapse: collapse;
            border: 1px solid black;
        }}
        th, td {{
            border: 1px solid black;
        }}
    </style>
</head>
<!-- Image Header -->
<header class="p-2 text-bg-dark">
    <div class="container">
      <div class="d-flex flex-wrap align-items-center justify-content-center justify-content-lg-start">
        <a href="/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
          <img class="me-3" src="/static/images/LogoNEXTGENDEM_Color_cropped.png" alt="logo_nextgem" width="40">
        </a>
        <span class="me-5 me-lg-auto fs-4 font-weight-bold" style="color:#FFFFFF;font-weight: 500;">NEXTGENDEM</span>
        <select id="rolViewChange" class="form-select mr-2" style="max-width: 15vh" onchange="this.options[this.selectedIndex].value && (window.location = this.options[this.selectedIndex].value);">
            <option selected value="/sessions/{sess_uuid}?page=main">Admin</option>
            <option value="/index.html?id={sess_uuid}">Guest</option>
        </select>
        <div class="text-end">
          <a href="https://demiurge.nextgendem.eu/" class="d-flex align-items-center mb-2 mb-lg-0 text-white text-decoration-none">
            <img class="me-3" src="/static/images/logo_demiurge.png" alt="logo_nextgem" width="40">
          </a>
        </div>
      </div>
    </div>
  </header>
<main class="d-flex flex-nowrap">
<div class="d-flex flex-column flex-shrink-0 p-3 text-bg-dark" style="width: 200px;min-height: 100vh; max-height: auto">
    <ul class="nav nav-pills flex-column mb-auto">
    """
    # check if it's admin or not
    is_admin = "transformer4-admin" in user_rol or "sys-admin" in user_rol
    is_super_admin = "sys-admin" in user_rol
    if page == "main":
        _ += f"""
      <form action="/sessions/{sess_uuid}?page=main" method="POST" id="mainRefresh" style="display: block">
      <button type="submit"
                    class="mb-3 bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                Refresh session
      </button>
      </form>
      <form action="/sessions/{sess_uuid}?page=sessions" method="POST" id="sessionRefresh" style="display: none">
      <button type="submit"
                    class="mb-3 bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                Refresh session
      </button>
      </form>"""
    else:
        _ += f"""
        <form action="/sessions/{sess_uuid}?page=main" method="POST" id="mainRefresh" style="display: none">
      <button type="submit"
                    class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                Refresh session
      </button>
      </form>
      <form action="/sessions/{sess_uuid}?page=sessions" method="POST" id="sessionRefresh" style="display: block">
      <button type="submit"
                    class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                Refresh session
      </button>
      </form>"""
        
    if is_admin:
      if page == "main":
        _ += f"""
      <li class="nav-item">
        <button id="buttonCreate" href="#" class="nav-link active" aria-current="page" onclick="hideAvailable()">
          My session
        </button>
      </li>
      <li id="availableSelector">
        <button id="buttonAvailable" href="#" class="nav-link text-white" onclick="hideCreate()">
          Available sessions
        </button>
      </li>
      """
      else:
        _ += f"""
      <li class="nav-item">
        <button id="buttonCreate" href="#" class="nav-link" aria-current="page" onclick="hideAvailable()">
          My session
        </button>
      </li><li id="availableSelector">
        <button id="buttonAvailable" href="#" class="nav-link text-white active" onclick="hideCreate()">
          Available sessions
        </button>
      </li>
      """

    _ += f"""
        </div>
    """ 

    if page == "main":
      _ += f"""
<div class="d-flex flex-column px-6 m-2 justify-center align-items-center" id="showSessions" style="max-width:85%">
    """
    else:
         _ += f"""
<div class="d-none d-flex flex-column px-6 m-2 justify-center align-items-center" id="showSessions">
    """
    _ += f"""
    <div class="flex p-4 m-6 justify-center">
        <h1 class="block text-gray-700 text-m font-bold mb-2">T4-Hub - Session</h1>
    </div>
    <div class="d-flex bg-white shadow-md rounded px-8 pt-6 pb-8 mb-4">
        <div class="d-flex flex-column mb-2 mx-3 justify-center align-items-center"">
            <label class="block text-sm blue-500 hover:blue-700 mb-2">
                <a onclick="openUrl('{s_local.url_path}', 'Slicer')" href="#" class="d-flex flex-column align-items-center">
                    <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" fill="black" class="bi bi-display" viewBox="0 0 16 16">
		            <path d="M0 4s0-2 2-2h12s2 0 2 2v6s0 2-2 2h-4q0 1 .25 1.5H11a.5.5 0 0 1 0 1H5a.5.5 0 0 1 0-1h.75Q6 13 6 12H2s-2 0-2-2zm1.398-.855a.76.76 0 0 0-.254.302A1.5 1.5 0 0 0 1 4.01V10c0 .325.078.502.145.602q.105.156.302.254a1.5 1.5 0 0 0 .538.143L2.01 11H14c.325 0 .502-.078.602-.145a.76.76 0 0 0 .254-.302 1.5 1.5 0 0 0 .143-.538L15 9.99V4c0-.325-.078-.502-.145-.602a.76.76 0 0 0-.302-.254A1.5 1.5 0 0 0 13.99 3H2c-.325 0-.502.078-.602.145"/>
		            </svg>
                    <p>Access Transformer4 - {s_local.user}</p>
                </a>
            </label>
        </div>
        <div class="d-flex flex-column mb-2 mx-3 justify-center align-items-center"">
            <label class="block text-sm blue-500 hover:blue-700 mb-2">
                <a onclick="openUrl('{f"/{s_local.uuid}-files/"}', 'File manager')" href="#" class="d-flex flex-column align-items-center">
            		<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" fill="black" class="bi bi-folder" viewBox="0 0 16 16">
			           <path d="M.54 3.87.5 3a2 2 0 0 1 2-2h3.672a2 2 0 0 1 1.414.586l.828.828A2 2 0 0 0 9.828 3h3.982a2 2 0 0 1 1.992 2.181l-.637 7A2 2 0 0 1 13.174 14H2.826a2 2 0 0 1-1.991-1.819l-.637-7a2 2 0 0 1 .342-1.31zM2.19 4a1 1 0 0 0-.996 1.09l.637 7a1 1 0 0 0 .995.91h10.348a1 1 0 0 0 .995-.91l.637-7A1 1 0 0 0 13.81 4zm4.69-1.707A1 1 0 0 0 6.172 2H2.5a1 1 0 0 0-1 .981l.006.139q.323-.119.684-.12h5.396z"/>
			        </svg>
                    <p>Access File Manager - {s_local.user}</p>
                </a>
            </label>
        </div>
    </div>
    <form class="bg-white shadow-md rounded px-8 pt-6 pb-8 mb-4" method="POST" action="/sessions/{sess_uuid}/close">
        <div class="flex items-center justify-between">
            <button type="submit"
                    class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                Close session
            </button>
        </div>
    </form>
"""
    print("sess_shared: " + str(s_local.info['shared']))
    if not s_local.info['shared']:
        _ += f"""
    <div class="flex p-4 m-6 justify-center">
        <form class="bg-white shadow-md rounded px-8 pt-6 pb-8 mb-4" method="POST" action="/sessions/{sess_uuid}/share?interactive=0">
            <div class="flex items-center justify-between">
                <button type="submit"
                        class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                    Share session URL (view-only)
                </button>
            </div>
        </form>
        <form class="bg-white shadow-md rounded px-8 pt-6 pb-8 mb-4" method="POST" action="/sessions/{sess_uuid}/share?interactive=1">
            <div class="flex items-center justify-between">
                <button type="submit"
                        class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                    Share session URL (interactive)
                </button>
            </div>
        </form>
    </div>
    """
    else:
      _ += f"""
    <div class="flex p-4 m-6 justify-center">
        <form class="bg-white shadow-md rounded px-8 pt-6 pb-4 mb-2" method="POST" action="/sessions/{sess_uuid}/unshare">
            <div class="flex items-center justify-between">
                <button type="submit"
                        class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                    Unshare session
                </button>
            </div>
        </form>
    </div>
    """
    _ += f"""
    <script>
        function hideCreate() {{
         document.getElementById("displaySessions").style.display = "block";
         document.getElementById("mainRefresh").style.display = "none";
         document.getElementById("sessionRefresh").style.display = "block";
         w3.addClass('#showSessions','d-none')
         w3.addClass('#buttonAvailable','active')
         w3.removeClass('#buttonCreate','active')
        }}
        function hideAvailable() {{
         document.getElementById("displaySessions").style.display = "none";
         document.getElementById("sessionRefresh").style.display = "none";
        document.getElementById("mainRefresh").style.display = "block";
         w3.removeClass('#showSessions','d-none')
         w3.addClass('#buttonCreate','active')
         w3.removeClass('#buttonAvailable','active')
        }}
    </script>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
    """
    for s in sess.query(AppSession).all():
            if is_super_admin:
              _ +=  f"""

<table>
    <thead>
        <tr>
            <th>UUID</th>
            <th>Creation</th>
            <th>Last activity</th>
            <th>User</th>
            <th>Email</th>
            <th>Container</th>
            <th>Restart</th>
            <th>GPU</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>{ s.uuid }</td>
            <td>{ s.created_at }</td>
            <td>{ s.last_activity }</td>
            <td>{ s.user }</td>
            <td>{ s.email }</td>
            <td>{ s.container_name }</td>
            <td>{ s.restart }</td>
            <td>{ s.gpu }</td>
            <td>
                <form method="POST" action="/sessions/{sess_uuid}/{s.uuid}/delete">
                    <div class="flex items-center justify-between">
                        <button type="submit"
                                class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline">
                            Delete session { s.user }
                        </button>
                    </div>
                </form>
            </td>
        </tr>
    </tbody>
    </table>
    """
            if admin or s.info["shared"]:
                # Section doing reverse proxy magic
                if s.info.get('shared_interactive', 0):
                    _url = s.url_path
                else:
                    _url = f"{s.url_path}/?view_only=true"
                _ += f"""
    </div>
    <div class="flex p-4 m-6 justify-center">
    """
                if page != "main":
                    _+=f"""
        <div id="displaySessions" class="p-3 w3-quarter" style="display: block">
        """
                else:
                    _+=f"""
        <div id="displaySessions" class="p-3 w3-quarter" style="display: none">
        """
                _ += f"""
        <a href="{_url}" target="_blank" rel="noopener noreferrer">
        <img src="/static/images/transformer4.png" alt="transformer4image" style="width:23%" class="w3-circle w3-hover-opacity">
        </a>
        <h3>{s.user}</h3>
        <p>CPU [%]: {s.info["CPU_pct"]}</p>
        <p>(last checked: {s.last_activity})</p>
        </div>
    </main>
    </div>
        """
    if index_path and write_to_file:
        with open(index_path, "wt") as f:
            f.write(_)
        logger.info(f"index.html re-written")
    return _

async def launch_app_web_container(s: AppSession):
    """
    Launch a AppSlicer web container
    """
    # just one container per user
    container_name = CONTAINER_NAME_PREFIX + container_orchestrator.get_valid_name(s.user)

    logger.info("CREATING NEW CONTAINER")
    container_orchestrator.create_image(app_image_name, app_image_tag)
    create_all_volumes(container_orchestrator, s.user)
    vol_dict = volume_dict(s.user)
    # await asyncio.sleep(5)
    c = await container_orchestrator.start_container(container_name, app_image_name, app_image_tag,
                                                     network_id, vol_dict, s.uuid, use_gpu = s.gpu)
    logs = c.logs
    # todo error control
    s.service_address = get_container_internal_address(container_orchestrator, container_name, network_id)
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

        tdslicer_containers = container_orchestrator.get_app_containers(CONTAINER_NAME_PREFIX)

        # Reassociate, restart or delete 3D Slicer Sessions if we are back from a restart of the container
        # Restart relaunches 3DSlicer ("restart" is always False, so this is disabled currently)
        # Delete
        with db_access_lock:
            sess = sm()
            for s in sess.query(AppSession).all():
                pct = container_orchestrator.get_container_activity(s.container_name)
                logger.info(f"pct container: {s.container_name}: {pct} ")
                s.last_activity = datetime.datetime.now()
                s.info['CPU_pct'] = pct
                if pct < 0:  # <0 -> "Container does not exist"
                    if s.restart:
                        # TODO right now "restart" is always False so this is never executed
                        logger.info(f"::::::::::::::::: sessions_checker - restarting container for user {s.user}")
                        await launch_app_web_container(s)
                        s.info['CPU_pct'] = ACTIVITY_THRESHOLD + 1
                        sess.add(s)
                    else:
                        logger.info(f"::::::::::::::::: sessions_checker - deleting session {s.user} because associated container does not exist")
                        sess.delete(s)
                else:  # "Container exists"
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
        await refresh_nginx(container_orchestrator, sess, nginx_config_path, domain, app_hub_address)

        # Remove dangling 3dslicer containers managed by 3dslicer-hub
        for name in tdslicer_containers:
            if name.startswith(CONTAINER_NAME_PREFIX):
                logger.info(f"::::::::::::::::: sessions_checker - removing container {name} with no associated session")
                stop_remove_container(name)

        # After initialization, infinite loop
        while True:
            with db_access_lock:
                print(f"Checking for inactive containers (to remove them). "
                      f"Inactivity time (secs): {allowed_inactivity_time_in_seconds}")
                sess = sm()
                # Loop all sessions, remove those that are not in use
                for s in sess.query(AppSession).all():
                    print(f"Session - Name: {s.container_name};\n UUID: {s.uuid};\n User: {s.user}\n")
                    stop = await check_session_activity(s)  # Implicit parameter: "s" (3dslicer session)
                    sess.add(s)
                    if stop:
                        logger.info(f"::::::::::::::::: sessions_checker - inactivity cleanup - stopping container {s.container_name}")
                        stop_remove_container(s.container_name)
                        sess.delete(s)
                        # Update nginx.conf and reread Nginx configuration
                        await refresh_nginx(container_orchestrator, sess, nginx_config_path, domain, app_hub_address)

                sess.commit()
                sess.close()
                print("Check finished --------------")
            await asyncio.sleep(60)


runner = BackgroundRunner()


@app.on_event("startup")
async def startup():
    asyncio.create_task(runner.sessions_checker(orm_session_maker))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", debug=True)
