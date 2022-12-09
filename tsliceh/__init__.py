import datetime
import os
import uuid

import docker
from python_on_whales import docker as docker_ow
from docker.errors import APIError
from sqlalchemy import Column, JSON, Boolean, String, DateTime, TypeDecorator, CHAR
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import UUID

from tsliceh.helpers import containers_status, get_container_ip


class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses
    CHAR(32), storing as stringified hex values.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(UUID())
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return "%.32x" % uuid.UUID(value).int
            else:
                # hexstring
                return "%.32x" % value.int

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                value = uuid.UUID(value)
            return value


class Base(object):
    pass


SQLAlchemyBase = declarative_base(cls=Base)


class Session3DSlicer(SQLAlchemyBase):
    __tablename__ = "sessions"
    uuid = Column(GUID, nullable=False, primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.datetime.now())
    last_activity = Column(DateTime, nullable=True)
    user = Column(String(64), unique=True, nullable=False)
    url_path = Column(String(1024), nullable=True)
    service_address = Column(String(1024), nullable=True)
    container_name = Column(String(128), nullable=True)
    restart = Column(Boolean, nullable=False, default=False)
    info = Column(JSON)


def create_local_orm(conn_str):
    from sqlalchemy import create_engine
    return create_engine(conn_str, echo=True, connect_args={"check_same_thread": False})


def create_session_factory(engine_):
    """ Return a session factory for a given engine """
    return scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine_))


def create_tables(engine_, declarative_base_=SQLAlchemyBase):
    """ Create tables of a declarative base using an engine """
    tables = declarative_base_.metadata.tables
    connection = engine_.connect()
    table_existence = [engine_.dialect.has_table(connection, tables[t].name) for t in tables]
    connection.close()
    if False in table_existence:
        declarative_base_.metadata.bind = engine_
        declarative_base_.metadata.create_all()


def get_ldap_adress(mode, openldap_name, net_id):
    if mode == "container":
        ldap_adress = get_container_ip(openldap_name, net_id) + ":389"
    else:
        ldap_adress = "localhost:389"
    return ldap_adress


def get_domain_name(mode, domain_name):
    if mode == "local":
        domain_name = domain_name + ":8000"
    return domain_name


# def connect_ldap_server(ldap_adress):
#     """
#     https://medium.com/analytics-vidhya/crud-operations-for-openldap-using-python-ldap3-46393e3122af
#     :param ldap_adress:
#     :return:
#     """
#     try:
#
#         # Provide the hostname and port number of the openLDAP
#         # TODO FIND ldap ip
#         server_uri = ldap_adress
#         server = Server(server_uri, get_info=ALL)
#         # username and password can be configured during openldap setup
#         connection = Connection(server,
#                                 user='cn=admin,dc=opendx,dc=org',
#                                 password="admin_pass")
#         bind_response = connection.bind()  # Returns True or False
#     except LDAPBindError as e:
#         connection = e
#         return connection
#
#
# #
# # # For groups provide a groupid number instead of a uidNumber
# def get_ldap_users(ldap_adress):
#     """
#     https://medium.com/analytics-vidhya/crud-operations-for-openldap-using-python-ldap3-46393e3122af
#     :return:
#     :ldap_adress: interal IP of the container
#     """
#     # Provide a search base to search for.
#     search_base = 'dc=testldap,dc=com'
#     # provide a uidNumber to search for. '*" to fetch all users/groups
#     search_filter = '(uidNumber=500)'
#
#     # Establish connection to the server
#     ldap_conn = connect_ldap_server(ldap_adress)
#     try:
#         # only the attributes specified will be returned
#         ldap_conn.searchsearch('dc=opendx,dc=org', '(uid=*)',
#                                attributes=['sn', 'cn', 'homeDirectory'],
#                                size_limit=0)
#         # search will not return any values.
#         # the entries method in connection object returns the results
#         results = ldap_conn.entries
#     except LDAPException as e:
#         results = e


def docker_compose_up():
    compose = docker_ow.compose.up(detach=True)
    for container in docker_ow.compose.ps():
        status = containers_status(container.name)
        print(f"{container.name} : {status}")
        if status == "exited":
            raise APIError(500, f"Error running {container.name} : status : {status}")


def create_docker_network(network_name):
    """
    A partir del nomber de red que aparece en .env crea una red.
    En el paso de que se hayan creado varias reds con este nombre, las borra y crea una nueva.
    :param network_name:
    :return: network_id
    TODO revisar si viene bien hacer borrón y cuenta nueva
    """
    dc = docker.from_env()
    # print("networks inside container " + dc.networks.list(names = network_name))
    networks_list = dc.networks.list(names=network_name)
    for n in networks_list:
        print(f"NETWORK {n.id}:{n.name}:")
        for c in n.containers:
            print(f"......{c.name}")
    if len(networks_list) > 0:
        if len(networks_list) > 1:
            for network in networks_list:
                # check if there are containers attached to the network
                if len(network.containers) == 0:
                    network.remove()
                networks_list = dc.networks.list(names=network_name)
    if len(networks_list) == 1:
        return networks_list[0].id
    elif len(networks_list) == 0:
        network = dc.networks.create(network_name, driver="bridge")
        return network.id
    else:
        raise APIError(500, details=f"There is more than one {network_name} network active")


def pull_tdslicer_image(image_name, image_tag):
    dc = docker.from_env()
    image_full_name = f"{image_name}:{image_tag}"
    images = dc.images.list()
    for image in images:
        if image_full_name in image.tags:
            print(f"image {image} already in the system")
            return
    try:
        dc.images.pull(image_name, tag=image_tag)
    except docker.errors.APIError as e:
        raise Exception(e)


def refresh_nginx(sess, nginx_cfg_path, domainn, tds_address):
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
    server_name  {domainn};

    location / {{
    proxy_pass http://{tds_address}/;
    }}

    """
        if sess:
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
        print("::::::::::::::::::::::::::::CREATING NEW NGINX FILE:::::::::::::::::::::::::::::::::::::::::")
        print(_)
        if nginx_cfg_path:
            with open(nginx_cfg_path, "wt") as f:
                f.write(_)

    def command_nginx_to_read_configuration():
        """
        Given the name of the NGINX container used as reverse proxy for 3DSlicer sessions,
        command it to reread the configuration.
        """
        nginx_container_name = os.getenv("NGINX_NAME")  # TODO Pass (inject) as parameter
        tries = 0
        while tries < 6:
            status = containers_status(nginx_container_name)
            if status == "running":
                dc = docker.from_env()
                nginx = dc.containers.get(nginx_container_name)
                # logger.info("RELOADING NGINX FILE")
                try:
                    r = nginx.exec_run("/etc/init.d/nginx reload")
                    # logger.info(r.output)
                    return r
                except docker.errors.APIError as e:
                    # logger.warning(e.response)
                    # logger.info("trying to reload nginx proxy")
                    for tries in range(5):
                        docker_compose_up()
                    raise Exception(500, "Error when reloading nginx.conf")


    # -----------------------------------------------

    generate_nginx_conf()
    command_nginx_to_read_configuration()
