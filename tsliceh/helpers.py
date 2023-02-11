from tsliceh.orchestrators import IContainerOrchestrator


def container_exists(name_id):
    # TODO CHECK IF CONTAINER EXISTS
    pass


# def check_ips(network_id):
#     dc = docker.from_env()
#     network = dc.networks.get(network_id)
#     for container in network.containers:
#         print(f"{container.name} : {container.attrs['NetworkSettings']['Networks'][network.name]['IPAddress']}")


# thanks to https://github.com/TomasTomecek/sen/blob/master/sen/util.py#L158
# change in cpu_count
def calculate_cpu_percent(d):
    cpu_count = float(d["cpu_stats"]["online_cpus"])  # how many cpus the container has
    cpu_percent = 0.0
    cpu_delta = float(d["cpu_stats"]["cpu_usage"]["total_usage"]) - \
                float(d["precpu_stats"]["cpu_usage"]["total_usage"])
    system_delta = float(d["cpu_stats"]["system_cpu_usage"]) - \
                   float(d["precpu_stats"]["system_cpu_usage"])
    if system_delta > 0.0:
        cpu_percent = cpu_delta / system_delta * 100.0 * cpu_count
    return cpu_percent


def get_container_internal_address(co: IContainerOrchestrator, name_id, network_id):
    ip = co.get_container_ip(name_id, network_id)
    port = co.get_container_port(name_id)
    return f"{ip}:{port}"


def containers_cpu_percent_dict(co: IContainerOrchestrator):
    d = []
    stats = co.container_stats()
    for stat in stats:
        cpu_perc = calculate_cpu_percent(stats)
        d.append({"container": stat["name"]}, {"cpu_percent": cpu_perc})
    return d
