from time import sleep

import docker


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


def get_container_internal_adress(name_id, network_id):
    ip = get_container_ip(name_id, network_id)
    port = get_container_port(name_id)
    return f"{ip}:{port}"


def container_exists(name_id):
    # TODO CHECK IF CONTAINER EXISTS
    pass


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


def check_ips(network_id):
    dc = docker.from_env()
    network = dc.networks.get(network_id)
    for container in network.containers:
        print(f"{container.name} : {container.attrs['NetworkSettings']['Networks'][network.name]['IPAddress']}")


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


# thanks to https://github.com/TomasTomecek/sen/blob/master/sen/util.py#L158
# cambio en cpu_count
def calculate_cpu_percent(d):
    cpu_count = float(d["cpu_stats"]["online_cpus"]) # cuántos cpus hay
    cpu_percent = 0.0
    cpu_delta = float(d["cpu_stats"]["cpu_usage"]["total_usage"]) - \
                float(d["precpu_stats"]["cpu_usage"]["total_usage"])
    system_delta = float(d["cpu_stats"]["system_cpu_usage"]) - \
                   float(d["precpu_stats"]["system_cpu_usage"])
    if system_delta > 0.0:
        cpu_percent = cpu_delta / system_delta * 100.0 * cpu_count
    return cpu_percent


def containers_cpu_percent_dict():
    d = []
    stats = container_stats()
    for stat in stats:
        cpu_perc = calculate_cpu_percent(stats)
        d.append({"container": stat["name"]}, {"cpu_percent": cpu_perc})
    return d
