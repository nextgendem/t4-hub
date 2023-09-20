import abc
import asyncio
import json
import subprocess
from time import sleep

import docker
from docker.errors import APIError
from fastapi.logger import logger
from python_on_whales import docker as docker_ow


# import kubernetes
# from kubernetes import client, config


class IContainerOrchestrator(abc.ABC):
    @abc.abstractmethod
    def get_tdscontainers(self, prefix):
        pass

    @abc.abstractmethod
    def create_network(self, network_name):
        pass

    @abc.abstractmethod
    def create_volume(self, name, type_):
        pass

    @abc.abstractmethod
    def remove_volume(self, volume_name):
        pass

    @abc.abstractmethod
    def get_container_activity(self, container_name):
        pass

    @abc.abstractmethod
    def get_container_ip(self, name_id, network_id):
        pass

    @abc.abstractmethod
    def get_container_port(self, name_id):
        pass

    @abc.abstractmethod
    def get_container_status(self, container_name):
        pass

    @abc.abstractmethod
    def get_container_stats(self, container_name):
        pass

    @abc.abstractmethod
    def container_exists(self, container_name):
        pass

    @abc.abstractmethod
    def create_container(self, container_name, image_name):
        pass

    @abc.abstractmethod
    async def start_container(self, container_name, image_name, image_tag,
                              network_id, vol_dict, wait_until_running):  # "run" also
        pass

    @abc.abstractmethod
    def stop_container(self, container_name):
        pass

    @abc.abstractmethod
    def remove_container(self, container_name):
        pass

    @abc.abstractmethod
    def create_image(self, image_name, image_tag):
        pass

    @abc.abstractmethod
    def execute_cmd_in_container(self, container_name, cmd):
        pass

    @abc.abstractmethod
    def start_base_containers(self):
        """
        NGINX and OpenLDAP; but may be others in the future
        :return:
        """
        pass


class DockerCompose(IContainerOrchestrator):
    def __init__(self, compose_file=None):
        self.compose_file = compose_file

    def get_tdscontainers(self, prefix=""):
        dc = docker.from_env()
        try:
            return [c.name for c in dc.containers.list(all) if c.name.startswith(prefix)]
        except Exception as e:
            logger.info(f"::::::::::::::::: sessions_checker - EXCEPTION no containers. {e}")
            return None

    def create_network(self, network_name):
        return create_docker_network(network_name)

    def create_volume(self, name, type_):
        create_volume(name, type_)

    def remove_volume(self, volume_name):
        remove_volume(volume_name)

    def get_container_activity(self, container_name):
        return docker_container_pct_activity(container_name)

    def get_container_ip(self, name_id, network_id):
        return get_container_ip(name_id, network_id)

    def get_container_port(self, name_id):
        return get_container_port(name_id)

    def get_container_status(self, name_id):
        return containers_status(name_id)

    def get_container_stats(self, container_name):
        return container_stats(container_name)

    def container_exists(self, container_name):
        pass

    def create_container(self, container_name, image_name):
        pass

    async def start_container(self, container_name, image_name, image_tag,
                              network_id, vol_dict,
                              wait_until_running=True):  # "run" also
        dc = docker.from_env()
        active = False
        c = dc.containers.run(image=f"{image_name}:{image_tag}",
                              environment={"VNC_DISABLE_AUTH":"true"},
                              # ports={"6901/tcp": None},
                              name=container_name,
                              network=network_id,
                              volumes=vol_dict,
                              detach=True,
                              user="root",
                              shm_size="512m")
        container_id = c.id
        if wait_until_running:
            while not active:
                # TODO mejorar: crear funcion check_state
                await asyncio.sleep(3)
                c = dc.containers.get(container_id)
                if c.status == "running":
                    active = True
                if c.status == "exited":
                    logger.info("container exited")
                    break
        return c

    def stop_container(self, name):
        """

        :param name:
        :return: True if the container exists and it is stopped. False if the container exists but it could not be stopped. None if the container does not exist
        """
        dc = docker.from_env()
        try:
            c = dc.containers.get(name)
            can_remove = False
            status = self.get_container_status(name)
            if status:
                if status == "running":
                    try:
                        c.stop()
                        c.reload()
                        can_remove = True
                    except:
                        stopped = False
                        can_remove = False
                        print(f"can't stop container {name}")
            if c.status == "exited" or can_remove:
                stopped = True
        except:
            logger.info(f"{name} container already removed")
            stopped = None

        return stopped

    def remove_container(self, name, force=False):
        dc = docker.from_env()
        try:
            c = dc.containers.get(name)
            c.remove(force=force)
            status = self.get_container_status(name)
            if not status:
                logger.info(f"container {name} : removed")
                removed = True
            else:
                logger.info(f"can't remove {name}")
                removed = False
        except:
            logger.info(f"{name} container already removed")
            removed = None
        return removed

    def create_image(self, image_name, image_tag):
        create_image(image_name, image_tag)

    def execute_cmd_in_container(self, container_name, cmd):
        dc = docker.from_env()
        nginx = dc.containers.get(container_name)
        try:
            r = nginx.exec_run("/etc/init.d/nginx reload")
            return r
        except docker.errors.APIError as e:
            return None

    def start_base_containers(self):
        docker_compose_up()


class Kubernetes(IContainerOrchestrator):
    def __init__(self):
        kubernetes.config.load_kube_config()
        self.client = kubernetes.client.CoreV1Api()

    @staticmethod
    def _exec_kubectl(cmd):
        # TODO If executed inside the K8s cluster, specify additional parameters
        # Execute cmd
        cmd = ["kubectl"] + cmd + ["-o", "json"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, capture_output=True, text=True)
        _ = proc.stdout
        try:
            d = json.loads(_)
        except:
            d = None
        return d

    def get_tdscontainers(self, prefix):
        ["get", "po"]
        # TODO query all pods, get only those starting with prefix
        ret = self.client.list_pod_for_all_namespaces()
        for i in ret.items:
            print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))

    def create_network(self):
        # TODO Create network for service pods if it does not already exist
        # TODO Manifest to create the network

        cmd = ["apply", "-f", "network.yaml"]
        self.__exec_kubectl(cmd)

    def create_volume(self, name, type_):
        pass

    def remove_volume(self, volume_name):
        pass

    def get_container_activity(self, container_name):
        pass

    def get_container_ip(self, name_id, network_id):
        pass

    def get_container_port(self, name_id):
        pass

    def get_container_status(self, container_name):
        pass

    def container_exists(self, container_name):
        pass

    def create_container(self, container_name, image_name):
        pass

    async def start_container(self, container_name, image_name, image_tag,
                              network_id, vol_dict, wait_until_running):
        # kubectl run <container_name> --image=<image_name>:<image_tag> --restart=Never
        # TODO How to indicate the network and the volumes?

        pass

    def stop_container(self, container_name):
        # kubectl to stop a kubernetes pod
        # TODO Just check the pod exists, a remove container will follow
        pass

    def remove_container(self, container_name):
        # kubectl delete pod <container_name>
        pass

    def create_image(self, image_name, image_tag):
        pass


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


def create_volume(name, type_):
    """
    create a volume for the first time
    :param name: Is the user name, name of the volume and container
    :param type_: the type of volume as, workspace or configuration
    :param label: Scome more information about the volume as
    :return:
    """
    dc = docker.from_env()
    try:
        volume = dc.volumes.get(f"{name}_{type_}")
    except docker.errors.NotFound:
        volume = dc.volumes.create(name=f"{name}_{type_}", driver='local')
        print(f"new volume {volume.name} created")
    except Exception as e:
        print(e.message, e.args)


def remove_volume(name):
    dc = docker.from_env()
    volume = dc.volumes.get(name)
    try:
        volume.remove()
    except docker.errors.APIError:
        container = dc.containers.get(name)
        if container.status == "running":
            print("The volume is attached to a working container")
    finally:
        print(f"cant remove volume {name}")


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
        from tsliceh.helpers import calculate_cpu_percent
        return calculate_cpu_percent(stats)
    except:
        return -1


def get_container_ip(name_id, network_id):
    # TODO get ip without network info possible..
    dc = docker.from_env()
    try:
        c = dc.containers.get(name_id)
        network = dc.networks.get(network_id)
        ip = c.attrs['NetworkSettings']['Networks'][network.name]['IPAddress']
    except:
        ip = ""
    return ip


def get_container_port(name_id):
    dc = docker.from_env()
    try:
        c = dc.containers.get(name_id)
        tmp = list(c.ports.keys())
        if len(tmp) > 0:
            port = tmp[-1].split('/')[0]
        else:
            port = ""
    except:
        port = ""
    return port


def containers_status(name_id):
    """
    Check if a container exist is running or exited or in case just created it waits until creation period is over
    :param name_id:
    :return: None, "runnung" or "exited
    """
    dc = docker.from_env()
    try:
        c = dc.containers.get(name_id)
        status = c.status
        if status == "running" or "exited":
            return status
        else:
            sleep(3)
            c.reload()
    except:
        return None


def container_stats(name_id=None):
    client = docker.DockerClient(base_url='unix:///var/run/docker.sock')  # esto debería ser una variable de env
    if name_id:
        container = client.containers.get(name_id)
        stats = container.stats(decode=None, stream=False)
    else:
        # todo throw list of cpus ussages
        stats = []
        for containers in client.containers.list():
            stats.append(containers.stats(decode=None, stream=False))
    return stats


def create_image(image_name, image_tag):
    dc = docker.from_env()
    image_full_name = f"{image_name}:{image_tag}"
    images = dc.images.list()
    # make a flat list of al tags
    tags = sum([image.tags for image in images], [])
    if image_full_name in tags:
        print(f"image {image_full_name} already in the system")
        return
    if image_full_name.startswith("opendx"):
        from tsliceh.main import base_vnc_image_url, tdslicer_image_name, tdslicer_image_url, base_vnc_image_name,base_vnc_image_tag
        base_vnc_image_full_name = f"{base_vnc_image_name}:{base_vnc_image_tag}"
        if base_vnc_image_full_name not in tags:
            dc.images.build(path=base_vnc_image_url, tag=base_vnc_image_name)
        dc.images.build(path=tdslicer_image_url, tag = tdslicer_image_name, buildargs = {"BASE_IMAGE":"vnc-base:latest"})
    else:
        try:
            dc.images.pull(image_name, tag=image_tag)
        except docker.errors.APIError as e:
            raise Exception(e)


def docker_compose_up():
    compose = docker_ow.compose.up(detach=True)
    for container in docker_ow.compose.ps():
        status = containers_status(container.name)
        print(f"{container.name} : {status}")
        if status == "exited":
            raise APIError(500, f"Error running {container.name} : status : {status}")
