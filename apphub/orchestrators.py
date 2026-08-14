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
from jinja2 import Template
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
    def get_app_containers(self, prefix):
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

    def get_app_containers(self, prefix=""):
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
    def __init__(self):
        self._port = 8080  # App Hub backend internal port
        self._app_label = "t4"  # TODO It should be a parameter
        self._template_file = os.getenv("APP_MANIFEST_TEMPLATE", "app-deployment-template2.yaml")
        # Storage configuration
        self._storage_type = os.getenv("STORAGE_TYPE", "hostPath")
        self._storage_base = os.getenv("STORAGE_BASE_PATH", "/tmp/t4hub-storage")
        self._nfs_server = os.getenv("NFS_SERVER", None)
        self._storage_class = os.getenv("STORAGE_CLASS", "gp3")
        self._namespace = os.getenv("K8S_NAMESPACE", "default")
        # Session sizing. The previous hardcoded 10-core request left sessions Pending on any
        # ordinary node; memory was unbounded, so one session could evict its neighbours.
        self._cpu_requested = os.getenv("T4_SESSION_CPU_REQUESTED", "1")
        self._cpu_limit = os.getenv("T4_SESSION_CPU_LIMIT", "4")
        self._mem_requested = os.getenv("T4_SESSION_MEM_REQUESTED", "1Gi")
        self._mem_limit = os.getenv("T4_SESSION_MEM_LIMIT", "4Gi")
        # "Never" only works where the image was imported into every node's containerd by hand.
        self._image_pull_policy = os.getenv("IMAGE_PULL_POLICY", "IfNotPresent")

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
                df = pd.read_table(StringIO(_), delimiter=r'\s\s+', engine="python")
                return df.to_dict("records")
            elif output_type.lower() == "json":
                return json.loads(_)
            elif output_type.lower() == "yaml":
                return yaml.load(_, Loader=yaml.FullLoader)
        except:
            return None

    def _container_action(self, container_name, image_name, vol_dict, network_id, uid, use_gpu = False, operation="apply"):
        # assign cpu resource to pod or container https://kubernetes.io/docs/tasks/configure-pod-container/assign-cpu-resource/
        ncores_cpu_limit = self._cpu_limit  # no podrá usar más de esto
        ncores_cpu_requested = self._cpu_requested  # cpu garanztizada
        mem_limit_value = self._mem_limit
        mem_requested_value = self._mem_requested

        # Use storage abstraction module to generate volumes
        from apphub.storage import get_storage_config, ensure_local_storage_directories

        container_vols = ""
        container_vol_mounts = ""
        if vol_dict is not None:
            # Generate volume specs based on configured storage type
            volumes_yaml, volume_mounts_yaml = get_storage_config(
                storage_type=self._storage_type,
                container_name=container_name,
                vol_dict=vol_dict,
                base_path=self._storage_base,
                nfs_server=self._nfs_server,
                storage_class=self._storage_class,
                namespace=self._namespace
            )

            # Indent for template insertion
            indentation = 8
            container_vols = textwrap.indent(volumes_yaml, " " * indentation)
            indentation = 10
            container_vol_mounts = textwrap.indent(volume_mounts_yaml, " " * indentation)

            # For hostPath storage, ensure directories exist before pod creation
            if self._storage_type == "hostPath":
                try:
                    ensure_local_storage_directories(container_name, vol_dict, self._storage_base)
                except Exception as e:
                    logger.warning(f"Could not create storage directories: {e}")

        if use_gpu:
            # 12 spaces, the same as the cpu/memory keys: siblings of a YAML mapping must line up.
            indent = " "*12
            nvidia_gpu = f"{indent}nvidia.com/gpu: 1"
            indent = " "*6
            ncores_cpu_requested = "0.5"
            ncores_cpu_limit = "4"
            gpu_toleration = "\n".join([f"{indent}tolerations:", 
                            f"{indent}- key: nvidia.com/gpu", 
                            f"{indent}  operator: Exists",
                            f"{indent}  effect: NoSchedule"])

        else:
            nvidia_gpu =""
            gpu_toleration=""

        indent_resource = " " * 10  # Indentation for limits/requests under resources
        indent_field = " " * 12     # Indentation for cpu/gpu under limits
        cpu_limit = f'{indent_field}cpu: "{ncores_cpu_limit}"'
        cpu_requested = f'{indent_field}cpu: "{ncores_cpu_requested}"'
        mem_limit = f'{indent_field}memory: "{mem_limit_value}"' if mem_limit_value else ""
        mem_requested = f'{indent_field}memory: "{mem_requested_value}"' if mem_requested_value else ""


        if "cpusinlimite" in container_name:
            cpu_limit = ""

        # Join only the non-empty lines: a blank line inside a YAML mapping is tolerated, but an
        # empty "limits:" block is not, and the GPU line is absent on every non-GPU session.
        limit_lines = [line for line in (cpu_limit, mem_limit, nvidia_gpu) if line]
        limits = "\n".join([f"{indent_resource}limits:"] + limit_lines) if limit_lines else ""
        cpu_requested = "\n".join([line for line in (cpu_requested, mem_requested) if line])

        def escape_for_sed_origin(text):
            """ Escapes special characters in a string for use with sed, including single quotes. """
            # Escaping special characters for sed
            # escape_chars = r"[]\/$*.^&"  # List of special characters to escape
            escaped_text = re.sub(r'([\[\]\/\$*\.^&])', r'\\\1', text)

            # Escaping single quotes for shell usage
            escaped_text = escaped_text.replace("'", "'\\''")

            return escaped_text

        def escape_for_sed_replace(text):
            # Doubling backslashes
            escaped_text = text.replace("(", "\\(")
            # Since the string will be in double quotes, no need to escape single quotes
            return escaped_text

        def escape_for_yaml(text):
            """ Escapes a string so it can be embedded in a YAML file, assuming the string is enclosed in double quotes. """
            # Doubling backslashes
            escaped_text = text.replace("\\", "\\\\")
            # Double quotes must be escaped too (the patches end up inside a YAML double-quoted scalar).
            # Done after the backslash doubling, so the backslash added here is not doubled in turn.
            escaped_text = escaped_text.replace('"', '\\"')
            # Since the string will be in double quotes, no need to escape single quotes
            return escaped_text

        # Patches to KASM
        src_code = escape_for_sed_origin("document.getElementById('noVNC_status').style")  # Unique
        new_code = escape_for_sed_replace("UI._sessionTimeoutInterval = setInterval(function () {UI.rfb.sendKey(1, null, false);}, 6000);")

        # KASM/noVNC on Firefox: navigator.clipboard.read exists (FF >= 125), so supportsBinaryClipboard()
        # returns true, and connect() then calls navigator.permissions.query({name: "clipboard-read"}) --
        # a permission name Firefox rejects. The call has no .catch(), so the TypeError escapes and the
        # session shows "noVNC encountered an error" instead of the desktop. Disable binary clipboard on
        # Firefox (Kasm documents it as Chromium-only anyway); Firefox falls back to the Clipboard panel.
        # isFirefox() is defined in the same bundle module, a few lines above the patched one.
        clip_src = escape_for_sed_origin("typeof navigator.clipboard.read")  # Unique
        clip_new = escape_for_sed_replace(
            '  return !isFirefox() && navigator.clipboard && typeof navigator.clipboard.read === "function";')

        # To test: docker
        patches = (f"sed -i 's/websockify/{uid}-ws/g' /usr/share/kasmvnc/www/app/ui.js && "
                   f"sed -i 's/websockify/{uid}-ws/g' /usr/share/kasmvnc/www/dist/main.bundle.js && "
                   f"sed -i '/{src_code}/c\\{new_code}' /usr/share/kasmvnc/www/app/ui.js && "
                   f"sed -i '/{src_code}/c\\{new_code}' /usr/share/kasmvnc/www/dist/main.bundle.js && "
                   # Only the bundle is served to the browser; core/util/browser.js is not
                   f"sed -i '/{clip_src}/c\\{clip_new}' /usr/share/kasmvnc/www/dist/main.bundle.js")
        patches = escape_for_yaml(patches)

        # Load the manifest from a file with replaceable strings
        with open(self._template_file, 'r') as f:
            template = Template(f.read())
            _ = template.render(
                container_name=container_name,
                app_label=self._app_label,
                container_vols=container_vols,
                mount_nfs_base=self._storage_base,  # Use configured storage base path
                image_name=image_name,
                image_pull_policy=self._image_pull_policy,
                patches=patches,
                limits=limits,
                cpu_requested=cpu_requested,
                container_vol_mounts=container_vol_mounts,
                gpu_toleration=gpu_toleration
            )

        # Write string to a temporary file
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            logger.debug(f"Manifest file content:\n{_}\n----------------")
            f.write(_)
            f.close()
            if operation == "apply":
                desc = "Create app-through-VNC-in-browser, apply Deployment manifest"
                cmd = ["apply", "-f", f.name]
            elif operation == "delete":
                desc = "Delete app-through-VNC-in-browser, delete Deployment manifest"
                cmd = ["delete", "-f", f.name]
            res = Kubernetes._exec_kubectl(desc, cmd)
            os.remove(f.name)
        return res

    def get_app_containers(self, prefix):
        """
        Obtain App instances, looking for Deployments (depends on the template launched with "_container_action")

        :param prefix:
        :return:
        """
        cmd = ["get", "deployments", "-l", f"app={self._app_label}"]
        res = Kubernetes._exec_kubectl("Get app-through-VNC-in-browser containers", cmd, "wide")
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

    @staticmethod
    def _pods_by_label(desc, label_selector):
        """
        Pod rows for a label selector, oldest first, so [-1] is the newest.

        A session Deployment can legitimately have more than one pod at a time: an eviction, an
        OOM-kill, or a rolling replacement all leave a dead pod alongside the live one. Sorting by
        creation time makes "the current pod" addressable instead of whatever kubectl happened to
        print first.
        """
        cmd = ["get", "pod", "-l", label_selector, "--sort-by=.metadata.creationTimestamp"]
        return Kubernetes._exec_kubectl(desc, cmd, "wide") or []

    @staticmethod
    def _select_live_pod(rows):
        """
        The newest pod that is actually serving, or None.

        Taking rows[0] blindly is what made the hub hang: after a node evicted a session pod for
        ephemeral-storage, `kubectl get pod -l app-user=...` listed the dead pod first, so the hub
        read Status=Error forever while a healthy pod sat right behind it.
        """
        running = [r for r in rows if str(r.get("STATUS", "")).strip() == "Running"]
        if not running:
            return None

        def _is_ready(row):
            value = str(row.get("READY", "")).strip()   # "1/1"
            if "/" in value:
                ready, total = value.split("/", 1)
                return ready == total
            return False

        ready = [r for r in running if _is_ready(r)]
        return (ready or running)[-1]

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
            # `kubectl top` cannot be sorted by creation time, and a leftover pod would drag the
            # reading down and get an active session culled. The busiest pod is the live one.
            cpus = []
            for row in res:
                try:
                    cpus.append(float(str(row["CPU(cores)"]).strip().rstrip("m")))
                except (KeyError, ValueError):
                    continue
            if not cpus:
                return -1
            logger.debug(f"-- Activity--: {res} -> max {max(cpus)}m")
            _ = (max(cpus) / 1000) * 100
            print(f"CPU %: {_}")
            return _

    def get_container_ip(self, name_id, network_id):
        rows = Kubernetes._pods_by_label("Get POD IP", f"app-user={name_id}")
        pod = Kubernetes._select_live_pod(rows)
        if pod is None:
            logger.debug(f"No Running pod for app-user={name_id} among {len(rows)} row(s)")
            return None
        logger.debug(f"IP: {pod['IP']}")
        return pod["IP"]

    def get_container_port(self, name_id):
        # Always the same port
        return self._port

    def get_container_status(self, container_name):
        # First try to get the pod directly by name (for infrastructure pods like proxy-app-hub)
        # If running inside a k8s pod, use POD_NAME env var if the requested container_name matches NGINX_NAME
        nginx_name = os.getenv("NGINX_NAME", "proxy-app-hub")
        pod_name = os.getenv("POD_NAME") if (container_name == nginx_name and os.getenv("POD_NAME")) else container_name
        
        print(f"DEBUG_PRINT: get_container_status for {container_name}, using pod_name {pod_name}")
        cmd = ["get", "pod", pod_name]
        res = Kubernetes._exec_kubectl("Get POD status by name", cmd, "wide")
        if res is not None:
            _ = res[0]["STATUS"]
            print(f"Status (by name): {_}")
            return _

        # Fall back to label-based search (for user session pods)
        rows = Kubernetes._pods_by_label("Get POD status by label", f"app-user={container_name}")
        if not rows:
            return "DoesNotExist"
        pod = Kubernetes._select_live_pod(rows)
        if pod is None:
            # Nothing Running: report the newest pod's own status rather than the oldest corpse's,
            # so a session that is still starting reads "Pending"/"ContainerCreating" and not
            # "Error" left over from a pod the node evicted.
            _ = str(rows[-1]["STATUS"]).strip()
        else:
            _ = str(pod["STATUS"]).strip()
        print(f"Status (by label): {_}")
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
        res = Kubernetes._exec_kubectl("Stop container, check deployment exists", cmd)
        if res is None:
            return False
        # Set the number of replicas to 0
        cmd = ["scale", "--replicas=0", f"deployment/deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Stop container, set RS replicas to 0", cmd)
        return True

    def restart_container(self, container_name):
        # First check the deployment exists
        cmd = ["get", "deployment", f"deploy-{container_name}"]
        res = Kubernetes._exec_kubectl("Restart container, check deployment exists", cmd)
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
        #
        # Deliberately NOT routed through _exec_kubectl: that helper feeds stdout to
        # pandas.read_table, and `nginx -s reload` writes nothing to stdout (its notices go to
        # stderr). The empty table raises, the bare except returns None, and the caller reads None
        # as "reload failed" and retries -- ten reloads in ~3 seconds, cycling nginx workers hard
        # enough to drop whatever request triggered the refresh (a POST /close would die with
        # ERR_NETWORK_CHANGED instead of following its redirect).
        pod_name = os.getenv("POD_NAME", "proxy-app-hub")
        argv = ["kubectl", "exec", pod_name, "-c", "nginx-container", "--", "sh", "-c", cmd]
        logger.debug(f"CMD Exec command in NGINX container: {' '.join(argv)}")
        proc = subprocess.run(argv, capture_output=True, text=True)
        logger.debug(f"  OUTPUT: {proc.stdout}\n  ERROR: {proc.stderr}\n  RC: {proc.returncode}\n----------------")
        if proc.returncode != 0:
            return None
        # Truthy on success, so the caller stops retrying. Success is commonly empty stdout.
        return proc.stdout or True

    def start_base_containers(self):
        """
        NGINX and OpenLDAP; but may be others in the future
        :return:
        """
        cmd = ["version"]  # No action
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
        from apphub.helpers import calculate_cpu_percent
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
    :return: None, "running" or "exited
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
    # TODO Modify the criterion to have image built (instead of pulled)
    if image_full_name.startswith("opendx"):
        from apphub.main import (app_image_name, app_image_url,
                                 base_vnc_image_name, base_vnc_image_url, base_vnc_image_tag)
        base_vnc_image_full_name = f"{base_vnc_image_name}:{base_vnc_image_tag}"
        if base_vnc_image_full_name not in tags:
            dc.images.build(path=base_vnc_image_url, tag=base_vnc_image_name)
        dc.images.build(path=app_image_url, tag=app_image_name, buildargs={"BASE_IMAGE": "vnc-base:latest"})
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
