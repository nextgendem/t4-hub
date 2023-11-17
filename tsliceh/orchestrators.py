import abc
import asyncio
import json
import os
import re
import subprocess
import tempfile
import textwrap
from time import sleep
from io import StringIO

import docker
import yaml
from docker.errors import APIError
from python_on_whales import docker as docker_ow
import pandas as pd
from fastapi.logger import logger


# import kubernetes
# from kubernetes import client, config


class IContainerOrchestrator(abc.ABC):
    @abc.abstractmethod
    def get_valid_name(self, name):
        pass

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
    async def start_container(self, container_name, image_name, image_tag,
                              network_id, vol_dict, uid, wait_until_running=None, use_gpu = False):  # "run" also
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
    def execute_cmd_in_nginx_container(self, container_name, cmd):
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

    def get_valid_name(self, name):
        return name

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

    async def start_container(self, container_name, image_name, image_tag,
                              network_id, vol_dict,
                              uid=None, wait_until_running=True, use_gpu = False):  # "run" also
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

    def execute_cmd_in_nginx_container(self, container_name, cmd):
        dc = docker.from_env()
        nginx = dc.containers.get(container_name)
        try:
            r = nginx.exec_run(cmd)
            return r
        except docker.errors.APIError as e:
            return None

    def start_base_containers(self):
        docker_compose_up()


class Kubernetes(IContainerOrchestrator):
    """
START
minikube start
cd /home/rnebot/GoogleDrive/AA_OpenDx28/3dslicerhub
kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
eval $(minikube docker-env)
docker build -t localhost:5000/opendx28/tslicerh . (like that the image will work with registry like in production)
docker run -d -p 5000:5000 --restart=always --name registry registry:2
docker push localhost:5000/opendx28/tslicerh 
 eval $(minikube docker-env --unset)
kubectl apply -f tsliceh/kubernetes/tdsh.yaml

DEPLOY / REDEPLOY
kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
eval $(minikube docker-env)
docker build -t opendx28/tslicerh .
 eval $(minikube docker-env --unset)
kubectl apply -f tsliceh/kubernetes/tdsh.yaml
kubectl logs -f proxy-shub

DEBUGGING
kubectl logs -f proxy-shub -c 3dslicer-hub
kubectl exec -ti proxy-shub -c 3dslicer-hub -- bash
kubectl get pods -l app=slicer -o wide
kubectl exec -ti proxy-shub -c nginx-container -- bash
kubectl logs -f proxy-shub -c nginx-container

URL OF THE SERVICE
minikube service my-service --url

kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
docker build -t opendx/tslicerh .
minikube image load opendx/tslicerh
kubectl apply -f tsliceh/kubernetes/tdsh.yaml
minikube service my-service --url
kubectl logs -f proxy-shub -c 3dslicer-hub

kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
kubectl apply -f tsliceh/kubernetes/tdsh.yaml
minikube service my-service --url
kubectl logs -f proxy-shub -c 3dslicer-hub

kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl apply -f tsliceh/kubernetes/tdsh.yaml
kubectl logs -f proxy-shub -c nginx-container

kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
kubectl apply -f tsliceh/kubernetes/tdsh.yaml
kubectl logs -f proxy-shub -c nginx-container

    """
    def __init__(self):
        self._port = 8080  # Slicer Hub backend internal port
        self._app_label = "slicer"

    def get_valid_name(self, name):
        # Replace "_" by "-"
        return name.replace("_", "-")

    @staticmethod
    def _exec_kubectl(desc, cmd, output_type=None):
        # Execute cmd
        if output_type is None:
            output = []
        elif output_type.lower() == "json":
            output = ["-o", "json"]
        elif output_type.lower() == "yaml":
            output = ["-o", "json"]
        elif output_type.lower() == "wide":
            output = ["-o", "wide"]

        # Build, execute, get output
        cmd = ["kubectl"] + cmd + output
        logger.debug(f"CMD {desc}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        _ = proc.stdout
        logger.debug(f"  OUTPUT: {_}\n")
        logger.debug(f"  ERROR: {proc.stderr}\n----------------")

        # Parse output
        try:
            if output_type is None or output_type.lower() == "wide":
                # Parse string as a list of dictionaries
                df = pd.read_table(StringIO(_), delimiter='\s\s+', engine="python")
                return df.to_dict("records")
            elif output_type.lower() == "json":
                return json.loads(_)
            elif output_type.lower() == "yaml":
                return yaml.load(_, Loader=yaml.FullLoader)
        except:
            return None

    def _container_action(self, container_name, image_name, vol_dict, network_id, uid, use_gpu = False, operation="apply"):
        # assign cpu resource to pod or container https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/ 
        cpu_limit = "8" # no podrá usar más de esto
        cpu_requested = "3" # cpu garanztizada

        mount_type = "NFS"
        mount_nfs_base = "/mnt/opendx28"
        if mount_type == "NFS":
            # Assume NODES have an NFS mount point with the same name in all nodes
            b_dir = f"{mount_nfs_base}/{container_name}/"
            # "volumes"
            _ = "\n".join([f"- name: vol-{container_name}-{i}\n  hostPath:\n    path: {b_dir}{i}" for i, (k, v) in enumerate(vol_dict.items())])
            indentation = 8
            container_vols = textwrap.indent(_, " " * indentation)
            # "volumeMounts"
            _ = "\n".join([f"- name: vol-{container_name}-{i}\n  mountPath: \"{v['bind']}\"" for i, (k, v) in enumerate(vol_dict.items())])
            indentation = 10
            container_vol_mounts = textwrap.indent(_, " " * indentation)
        if use_gpu:
            indent = " "*16
            nvidia_gpu = f"{indent}nvidia.com/gpu: 1"
            indent = " "*6
            cpu_requested = "0.5"
            cpu_limit = "4"
            gpu_toleration = "\n".join([f"{indent}tolerations:", 
                            f"{indent}- key: nvidia.com/gpu", 
                            f"{indent}  operator: Exists",
                            f"{indent}  effect: NoSchedule"])
        else:
            nvidia_gpu =""
            gpu_toleration=""
                                    
            

        # Generate a manifest file, apply it, remove the manifest
        _ = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deploy-{container_name}
  labels:
    app: {self._app_label}
spec:
  replicas: 1
  selector:
    matchLabels:
      app-user: {container_name}
  template:
    metadata:
      labels:
        app: {self._app_label}
        app-user: {container_name}
    spec:
      volumes:
{container_vols}    
        - name: config
          hostPath:
            path: {mount_nfs_base}/config-3dslicerhub
            type: Directory        
      containers:
      - name: {container_name}
        image: {image_name}
        imagePullPolicy: Always
        lifecycle:
          postStart:
            exec:
              command: ["/bin/sh", "-c", "sed -i 's/websockify/{uid}-ws/g' /usr/share/kasmvnc/www/app/ui.js && sed -i 's/websockify/{uid}-ws/g' /usr/share/kasmvnc/www/dist/main.bundle.js"]
        securityContext:
          runAsUser: 0 # Run as root user
        resources:
            limits:
                cpu: "{cpu_limit}"
{nvidia_gpu}
            requests:
                cpu: "{cpu_requested}"
        env:
        - name: VNC_DISABLE_AUTH
          value: "true"
        volumeMounts:
          - name: config
            mountPath: /etc/kasmvnc/
{container_vol_mounts}        
        ports:
        - containerPort: 6901
        - containerPort: 8085
{gpu_toleration}
                
        """

        # Write string to a temporary file
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            logger.debug(f"Manifest file content:\n{_}\n----------------")
            f.write(_)
            f.close()
            if operation == "apply":
                desc = "Create Slicer, apply Deployment manifest"
                cmd = ["apply", "-f", f.name]
            elif operation == "delete":
                desc = "Delete Slicer, delete Deployment manifest"
                cmd = ["delete", "-f", f.name]
            res = Kubernetes._exec_kubectl(desc, cmd)
            os.remove(f.name)
        return res

    def get_tdscontainers(self, prefix):
        """
        Obtain 3d slicer instances, looking for Deployments (depends on the template launched with "_container_action")

        :param prefix:
        :return:
        """
        cmd = ["get", "deployments", "-l", f"app={self._app_label}"]
        res = Kubernetes._exec_kubectl("Get Slicer containers", cmd, "wide")
        _ = []
        if res is not None:
            for i in res:
                deployment_name = i["NAME"]
                _.append(deployment_name[len("deploy-"):])
        return _

    def create_network(self):
        # TODO Create network for service pods if it does not already exist
        # TODO Manifest to create the network
        # cmd = ["apply", "-f", "network.yaml"]
        # self.__exec_kubectl(cmd)
        pass

    def create_volume(self, name, type_):
        # TODO Create an NFS volume and Volume Claim
        pass

    def remove_volume(self, volume_name):
        cmd = ["delete", "pvc", "--all", f"pvc-{volume_name}"]
        res = Kubernetes._exec_kubectl("Remove vol (delete Claim)", cmd)
        cmd = ["delete", "pv", "--all", volume_name]
        res = Kubernetes._exec_kubectl("Remove vol (delete Vol)", cmd)

    def get_container_activity(self, container_name):
        # Check if the deployment exists
        cmd = ["get", "deployment", f"deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Get activity, check deployment exists", cmd, "wide")
        if res is None:
            return -1

        # Obtain the CPU usage
        cmd = ["top", "pod", "-l", f"app-user={container_name}"]  # -> CPU, MEMORY
        res = Kubernetes._exec_kubectl("Get activity, get pod activity", cmd)
        if res is None or len(res) == 0:
            return -1
        else:
            logger.debug(f"-- Activity--: {res[0]}")
            _ = res[0]["CPU(cores)"]
            print(f"CPU: {_}")
            _ = (float(_[:-1]) / 1000) * 100
            print(f"CPU %: {_}")
            return _

    def get_container_ip(self, name_id, network_id):
        cmd = ["get", "pod", "-l", f"app-user={name_id}"]  # IP
        res = Kubernetes._exec_kubectl("Get POD IP", cmd, "wide")
        if res is None:
            return None
        _ = res[0]
        if _["STATUS"] == "Running":
            _ = _["IP"]
        else:
            logger.debug(f"Status: {_['STATUS']} not RUNNING")
            _ = None
        logger.debug(f"IP: {_}")
        return _

    def get_container_port(self, name_id):
        # Always the same port
        return self._port

    def get_container_status(self, container_name):
        cmd = ["get", "pod", "-l", f"app-user={container_name}"]
        res = Kubernetes._exec_kubectl("Get POD status", cmd, "wide")
        if res is None:
            return "DoesNotExist"
        _ = res[0]["STATUS"]
        print(f"Status: {_}")
        return _

    async def start_container(self, container_name, image_name, image_tag,
                              network_id=None, vol_dict=None, uid=None, wait_until_running=True, use_gpu = False):
        # TODO How to indicate the network and the volumes?
        logger.debug(f"Network id 2: {network_id}")

        class Object(object):
            pass

        c = Object()
        c.id = container_name  # Set to value used by "get_container_ip" (and get_container_port) <<
        c.name = container_name
        c.logs = None
        active = False
        self._container_action(container_name, f"{image_name}:{image_tag}", vol_dict, network_id, uid, use_gpu =use_gpu)
        if wait_until_running:
            while not active:
                await asyncio.sleep(3)
                c.status = self.get_container_status(container_name)
                if c.status.lower() == "running":
                    active = True
                    logger.info("container running")
                elif c.status.lower() == "exited":
                    logger.info("container exited")
                    break
        return c

    def stop_container(self, container_name):
        # First check the deployment exists
        cmd = ["get", "deployment", f"deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Stop container, check dpl exists", cmd)
        if res is None:
            return False
        # Set the number of replicas to 0
        cmd = ["scale", "--replicas=0", f"deployment/deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Stop container, set RS replicas to 0", cmd)
        return True

    def restart_container(self, container_name):
        # First check the deployment exists
        cmd = ["get", "deployment", f"deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Restart container, check dpl exists", cmd)
        if res is None:
            return
        # Set the number of replicas to 1
        cmd = ["scale", "--replicas=1", f"deployment/deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Restart container, set RS replicas to 1", cmd)

    def remove_container(self, container_name):
        cmd = ["delete", "deployment", f"deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Remove deployment", cmd)

    def create_image(self, image_name, image_tag):
        # TODO
        #  For minikube, execute:
        #  minikube image load <image_name>:<image_tag>
        pass

    def execute_cmd_in_nginx_container(self, container_name, cmd):
        # "container_name" is ignored, always "nginx-container"
        _ = ["exec", "proxy-shub", "-c", "nginx-container", "--"] + ["sh", "-c", cmd]
        return Kubernetes._exec_kubectl("Exec command in NGINX container", _)

    def start_base_containers(self):
        """
        NGINX and OpenLDAP; but may be others in the future
        :return:
        """
        cmd = ["apply", "-f", "tdsh.yaml"]
        return Kubernetes._exec_kubectl("Start base containers", cmd)


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
    client = docker.DockerClient(base_url='unix:///var/run/docker.sock')
    if name_id:
        container = client.containers.get(name_id)
        stats = container.stats(decode=None, stream=False)
    else:
        # todo throw list of cpus usages
        stats = []
        for containers in client.containers.list():
            stats.append(containers.stats(decode=None, stream=False))
    return stats


def create_image(image_name, image_tag):
    dc = docker.from_env()
    image_full_name = f"{image_name}:{image_tag}"
    images = dc.images.list()
    tags = sum([image.tags for image in images], [])
    if image_full_name in tags:
        print(f"image {image_full_name} already in the system")
        return
    if image_full_name.startswith("opendx"):
        from tsliceh.main import (tdslicer_image_name, tdslicer_image_url,
                                  base_vnc_image_name, base_vnc_image_url,  base_vnc_image_tag)
        base_vnc_image_full_name = f"{base_vnc_image_name}:{base_vnc_image_tag}"
        if base_vnc_image_full_name not in tags:
            dc.images.build(path=base_vnc_image_url, tag=base_vnc_image_name)
        dc.images.build(path=tdslicer_image_url, tag=tdslicer_image_name, buildargs={"BASE_IMAGE": "vnc-base:latest"})
        # TODO PUSH TO localhost:5000 respository (seams that is not supported)
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


def container_orchestrator_factory(s) -> IContainerOrchestrator:
    """
    Factory method for container orchestrators
    :param s: orchestrator name
    :return: orchestrator object
    """
    if s.lower() in ("docker", "docker_compose"):
        return DockerCompose()
    elif s.lower() == "kubernetes":
        return Kubernetes()
    else:
        raise Exception(f"Orchestrator {s} not implemented")
