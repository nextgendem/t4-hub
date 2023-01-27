import os

import docker
import docker.errors

vol_dict = {"cache_apt": "/var/cache/apt", # este tieme que ser borrado periodicamente? realmente lo necesito??
            # "tmpfiles": "/tmp", # todo parece que da problemas cuando le pongo ese volumen... quizás podría hacer que se destruya siembre
            "logs": "/var/log",
            "Documents": "/home/researcher/Documents"}
            # "/home/paula/Documentos/opendx28/3dslicerhub/researcher": "/home/resercher"}


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


def create_all_volumes(user):
    l = [k for k, _ in vol_dict.items()]
    for t in l:
        create_volume(user, t)


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


def volume_dict(user):
    d = dict()
    for k, v in vol_dict.items():
        # {"pmoreno_workspace": {"bind":"/var/cache/apt", "mode":"ro"}}
        d.update({f"{user}_{k}": {"bind": v, "mode": "rw"}}) # modes??
    # now Slicer.ini is not modifiable by the user... this is a kind of general configuration
    # TODO CREATE A {USER_ID} SLICER.INI (managing persistence)
    # from tsliceh.main import slicer_ini
    # d.update({slicer_ini: {"bind": "/home/researcher/.config/NA-MIC/Slicer.ini", "mode": "ro"}
    #           })
    return d
