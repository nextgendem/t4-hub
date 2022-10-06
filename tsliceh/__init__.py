import datetime
import uuid

import docker
from docker.errors import APIError
from sqlalchemy import Column, JSON, Boolean, String, DateTime, TypeDecorator, CHAR
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import UUID



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
    url_path = Column(String(1024), nullable=False)
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
    networks_list = dc.networks.list(names = network_name)
    if len(networks_list)>0:
       if len(networks_list) > 1:
            for network in networks_list:
                # check if there are containers attached to the network
                if len(network.containers) == 0:
                    network.remove()
                networks_list = dc.networks.list(names = network_name)
    if len(networks_list) == 1:
        return networks_list[0].id
    elif len(networks_list) == 0:
        network = dc.networks.create(network_name, driver="bridge")
        return network.id
    else:
        raise APIError(500, details = f"There is more than one {network_name} network active")
