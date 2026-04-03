import datetime
import os
import uuid

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
                print(value)
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


class AppSession(SQLAlchemyBase):
    __tablename__ = "sessions"
    uuid = Column(GUID, nullable=False, primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.now())
    last_activity = Column(DateTime, nullable=True)
    user = Column(String(64), unique=True, nullable=False)
    email = Column(String(64), unique=True, nullable=False)
    url_path = Column(String(1024), nullable=True)
    service_address = Column(String(1024), nullable=True)
    container_name = Column(String(128), nullable=True)
    restart = Column(Boolean, nullable=False, default=False)
    gpu = Column(Boolean, nullable=False, default=False)
    info = Column(JSON)

    @property
    def other_address(self):
        parts = self.service_address.split(':')
        alt_port = 8085  # Files web port
        parts[-1] = str(alt_port)
        return ':'.join(parts)


def create_local_orm(conn_str):
    from sqlalchemy import create_engine
    if "postgresql" in conn_str:
        args = {}
    else:
        args = {"check_same_thread": False}
    return create_engine(conn_str, echo=True, connect_args=args)


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


def get_domain_name(mode, domain_name, port=None):
    from dotenv import load_dotenv
    if mode == "local":
        return domain_name + f":{port if port is not None else 8000}"
    else:
        externalIP  = os.popen('curl -s ifconfig.me').readline()
        print(externalIP)
        load_dotenv()
        if externalIP == os.getenv("IP"):
            return os.getenv("DOMAIN")
        else:
            return "localhost"



