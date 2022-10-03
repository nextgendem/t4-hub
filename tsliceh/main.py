"""
3DSlicer Hub aims to imitate the functionality of JupyterHub, but for 3DSlicer:
  - Provide a login mechanism and a login page (ideally connected to an LDAP server)
  - Provide a way to launch 3DSlicer instances (in the future with a specific configuration)
  - Stop unused 3DSlicer instances
  - Integrate with a reverse proxy providing a single entry point for the users
  - Provide a way to share 3DSlicer instances
  - Persistent storage for new containers
"""
import asyncio
import datetime
import os

import docker
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

from tsliceh import create_session_factory, create_local_orm, Session3DSlicer, create_tables

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


nginx_container_name = None  # TODO Read from environment variable the name of nginx container relative to this container
nginx_config_path = None  # TODO Read from environment the location of nginx.conf relative to this container
allowed_inactivity_time_in_seconds = 600  # TODO Read from environment


# Welcome & login page
@app.get("/login")
async def welcome_and_login_page(request: Request):
    # Jinja2 template with login page
    _ = dict(request=request)
    return templates.TemplateResponse("login.html", _)


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
                s.url_path = f"{s.uuid}/"  # TODO Complete
                # Launch new container
                launch_3dslicer_web_docker_container(s)
                # Commit new
                session.add(s)
                session.commit()
                # Update nginx.conf and reread Nginx configuration
                refresh_nginx(session, nginx_config_path, nginx_container_name)
            return RedirectResponse(url='/redirect_example', status_code=302)  # TODO URL ??
        else:
            raise Exception(f"User {username} not authorized to open a 3DSlicer session")


def refresh_nginx(sess, nginx_cfg_path, nginx_cont_name):
    def generate_nginx_conf():
        """ For each session, generate a section, plus the first part """
        # TODO "nginx.conf" prefix
        _ = """
    ...    
    """
        for s in sess.query(Session3DSlicer).all():
            # TODO Section doing reverse proxy magic
            _ += f"""

            {s.uuid}
            {s.url_path}

    """
        if nginx_cfg_path:
            with open(nginx_cfg_path, "wt") as f:
                f.write(_)

    def command_nginx_to_read_configuration():
        """
        Given the name of the NGINX container used as reverse proxy for 3DSlicer sessions,
        command it to reread the configuration.
        """
        # TODO - nginx_cont_name
        pass

    # -----------------------------------------------

    generate_nginx_conf()
    command_nginx_to_read_configuration()


def launch_3dslicer_web_docker_container(s: Session3DSlicer):
    """
    Launch a 3DSlicer web container
    """
    # TODO
    pass


def stop_docker_container(s: Session3DSlicer):
    dc = docker.from_env()
    c = dc.containers.get(s.container_name)
    can_remove = False
    if c.status == "running":
        try:
            c.stop(timeout=60)
            can_remove = True
        except:
            pass
    if c.status == "exited" or can_remove:
        c.remove()


def docker_container_pct_activity(container_id):
    def container_exists(name):
        """
        Check if container exists (if not, it needs to be created)

        AN EXAMPLE OF "DOCKER" PACKAGE USAGE

        :param name:
        :return:
        """
        try:
            dc = docker.from_env()
            c = dc.containers.get(name)
            exists = True
            c.status  # created, exited, running
        except docker.errors.NotFound:
            exists = False
        return exists

    """
    Obtain the percentage of activity of a container
    -1 if the container does not exist

    :param container_id:
    :return:
    """
    # TODO
    return 6


class BackgroundRunner:
    def __init__(self):
        self.session_maker = None

    async def check_session_activity(self, s: Session3DSlicer, db_sess):
        container_id = s.uuid
        pct = docker_container_pct_activity(container_id)
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
            container_id = s.uuid
            pct = docker_container_pct_activity(container_id)
            if pct < 0:
                if s.restart:
                    launch_3dslicer_web_docker_container(s)
                else:
                    sess.delete(s)
        sess.commit()
        # Update nginx.conf and reread Nginx configuration
        refresh_nginx(sess, nginx_config_path, nginx_container_name)

        while True:
            sess = self.session_maker()
            # Loop all containers
            for s in sess.query(Session3DSlicer).all():
                stop = await self.check_session_activity(s, sess)
                if stop:
                    await stop_docker_container(s)
                    sess.delete(s)
                    # Update nginx.conf and reread Nginx configuration
                    refresh_nginx(sess, nginx_config_path, nginx_container_name)

            sess.commit()
            await asyncio.sleep(60)


runner = BackgroundRunner()


@app.on_event("startup")
async def startup():
    asyncio.create_task(runner.sessions_checker(orm_session_maker))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0")
