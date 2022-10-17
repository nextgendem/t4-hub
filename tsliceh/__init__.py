import datetime
import uuid

import docker
from python_on_whales import docker as docker_ow
from docker.errors import APIError
from sqlalchemy import Column, JSON, Boolean, String, DateTime, TypeDecorator, CHAR
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import UUID

# from ldap3 import Server, Connection, ALL, SUBTREE
# from ldap3.core.exceptions import LDAPException, LDAPBindError
#
from tsliceh.helpers import containers_status


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
    return create_engine(conn_str, echo=True)


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


# def connect_ldap_server():
#     try:
#
#         # Provide the hostname and port number of the openLDAP
#         # TODO FIND ldap ip
#         server_uri = f"ldap://192.168.1.3:389"
#         server = Server(server_uri, get_info=ALL)
#         # username and password can be configured during openldap setup
#         connection = Connection(server,
#                                 user='cn=admin,dc=testldap,dc=com',
#                                 password=PASSWORD)
#         bind_response = connection.bind()  # Returns True or False
#     except LDAPBindError as e:
#         connection = e
#
#
# # For groups provide a groupid number instead of a uidNumber
# def get_ldap_users():
#     # Provide a search base to search for.
#     search_base = 'dc=testldap,dc=com'
#     # provide a uidNumber to search for. '*" to fetch all users/groups
#     search_filter = '(uidNumber=500)'
#
#     # Establish connection to the server
#     ldap_conn = connect_ldap_server()
#     try:
#         # only the attributes specified will be returned
#         ldap_conn.search(search_base=search_base,
#                          search_filter=search_filter,
#                          search_scope=SUBTREE,
#                          attributes=['cn', 'sn', 'uid', 'uidNumber'])
#         # search will not return any values.
#         # the entries method in connection object returns the results
#         results = connection.entries
#     except LDAPException as e:
#         results = e


def docker_compose_up():
    compose = docker_ow.compose.up(detach=True)
    for container in docker_ow.compose.ps():
        status = containers_status(container.name)
        print(f"{container.name} : {status}")
        if status == "exited":
            raise APIError(500, f"Error running {container.name} : status : {status} \n logs: {container.logs(tail=10)}")


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
