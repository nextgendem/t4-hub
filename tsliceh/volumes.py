from tsliceh.orchestrators import IContainerOrchestrator

vol_dict = {"cache_apt": "/var/cache/apt", # este tieme que ser borrado periodicamente? realmente lo necesito??
            # "tmpfiles": "/tmp", # todo parece que da problemas cuando le pongo ese volumen... quizás podría hacer que se destruya siembre
            "logs": "/var/log",
            "Documents": "/home/researcher/Documents"}
            # "/home/paula/Documentos/opendx28/3dslicerhub/researcher": "/home/resercher"}


def create_all_volumes(co: IContainerOrchestrator, user):
    l = [k for k, _ in vol_dict.items()]
    for t in l:
        co.create_volume(user, t)


def volume_dict(user):
    d = dict()
    for k, v in vol_dict.items():
        # {"pmoreno_workspace": {"bind":"/var/cache/apt", "mode":"ro"}}
        d.update({f"{user}_{k}": {"bind": v, "mode": "rw"}})  # modes??
    # now Slicer.ini is not modifiable by the user... this is a kind of general configuration
    # TODO CREATE A {USER_ID} SLICER.INI (managing persistence)
    # from tsliceh.main import slicer_ini
    # d.update({slicer_ini: {"bind": "/home/researcher/.config/NA-MIC/Slicer.ini", "mode": "ro"}
    #           })
    return d
