"""
Storage abstraction module for t4-hub Kubernetes deployments.

Supports multiple storage backends:
- hostPath: Local directories (k3s local, k3s remote)
- nfs: NFS mounts (Teide HPC cluster)
- ebs: AWS EBS PersistentVolumeClaims (EKS)
"""
import os
import textwrap
from typing import Tuple, Dict, List, Optional
from fastapi.logger import logger


def get_storage_config(
    storage_type: str,
    container_name: str,
    vol_dict: Optional[Dict] = None,
    base_path: str = "/tmp/t4hub-storage",
    nfs_server: Optional[str] = None,
    storage_class: str = "gp3",
    namespace: str = "default"
) -> Tuple[str, str]:
    """
    Generate Kubernetes volume and volumeMount specifications based on storage type.

    Args:
        storage_type: One of "hostPath", "nfs", "ebs"
        container_name: Name of the container (used for directory/PVC naming)
        vol_dict: Dictionary mapping volume names to mount specs
                  e.g., {"cache_apt": {"bind": "/var/cache/apt", "mode": "rw"}}
        base_path: Base directory path for hostPath/nfs storage
        nfs_server: NFS server address (required for nfs type)
        storage_class: Kubernetes StorageClass name (for ebs type)
        namespace: Kubernetes namespace (for ebs type)

    Returns:
        Tuple of (volumes_yaml, volume_mounts_yaml) as indented YAML strings
    """
    if vol_dict is None:
        return "", ""

    if storage_type == "hostPath":
        return _get_hostpath_volumes(container_name, vol_dict, base_path)
    elif storage_type == "nfs":
        if not nfs_server:
            raise ValueError("nfs_server must be provided for NFS storage type")
        return _get_nfs_volumes(container_name, vol_dict, base_path, nfs_server)
    elif storage_type == "ebs":
        return _get_ebs_pvc_volumes(container_name, vol_dict, namespace, storage_class)
    else:
        raise ValueError(f"Unsupported storage type: {storage_type}")


def get_config_volume(
    storage_type: str,
    base_path: str = "/tmp/t4hub-storage",
    nfs_server: Optional[str] = None,
    config_pvc_name: str = "t4hub-config-pvc"
) -> str:
    """
    Generate volume spec for shared config directory (config-apphub).

    Returns:
        YAML string for the config volume (indented for insertion into spec.volumes)
    """
    if storage_type == "hostPath":
        return f"""- name: config
  hostPath:
    path: {base_path}/config-apphub
    type: DirectoryOrCreate"""
    elif storage_type == "nfs":
        return f"""- name: config
  nfs:
    server: {nfs_server}
    path: {base_path}/config-apphub"""
    elif storage_type == "ebs":
        # For EBS, config should be a shared PVC (created separately)
        return f"""- name: config
  persistentVolumeClaim:
    claimName: {config_pvc_name}"""
    else:
        raise ValueError(f"Unsupported storage type: {storage_type}")


def _get_hostpath_volumes(
    container_name: str,
    vol_dict: Dict,
    base_path: str
) -> Tuple[str, str]:
    """
    Generate hostPath volume specifications for local k3s deployments.

    Creates directory structure: {base_path}/{container_name}/{vol_name}/
    """
    volumes = []
    volume_mounts = []

    for i, (vol_name, vol_spec) in enumerate(vol_dict.items()):
        vol_k8s_name = f"vol-{container_name}-{i}"
        host_dir = f"{base_path}/{container_name}/{vol_name}"

        # Volume definition
        volumes.append(
            f"- name: {vol_k8s_name}\n"
            f"  hostPath:\n"
            f"    path: {host_dir}\n"
            f"    type: DirectoryOrCreate"
        )

        # VolumeMount definition
        volume_mounts.append(
            f"- name: {vol_k8s_name}\n"
            f"  mountPath: \"{vol_spec['bind']}\""
        )

    volumes_yaml = "\n".join(volumes)
    volume_mounts_yaml = "\n".join(volume_mounts)

    return volumes_yaml, volume_mounts_yaml


def _get_nfs_volumes(
    container_name: str,
    vol_dict: Dict,
    base_path: str,
    nfs_server: str
) -> Tuple[str, str]:
    """
    Generate NFS volume specifications for NFS-backed clusters.

    Assumes NFS server exports {base_path} and directories exist.
    """
    volumes = []
    volume_mounts = []

    for i, (vol_name, vol_spec) in enumerate(vol_dict.items()):
        vol_k8s_name = f"vol-{container_name}-{i}"
        nfs_path = f"{base_path}/{container_name}/{vol_name}"

        # Volume definition
        volumes.append(
            f"- name: {vol_k8s_name}\n"
            f"  nfs:\n"
            f"    server: {nfs_server}\n"
            f"    path: {nfs_path}"
        )

        # VolumeMount definition
        volume_mounts.append(
            f"- name: {vol_k8s_name}\n"
            f"  mountPath: \"{vol_spec['bind']}\""
        )

    volumes_yaml = "\n".join(volumes)
    volume_mounts_yaml = "\n".join(volume_mounts)

    return volumes_yaml, volume_mounts_yaml


def _get_ebs_pvc_volumes(
    container_name: str,
    vol_dict: Dict,
    namespace: str,
    storage_class: str
) -> Tuple[str, str]:
    """
    Generate PVC-based volume specifications for AWS EKS with EBS.

    Note: This returns volume references. Actual PVCs must be created separately
    by the orchestrator before creating the Deployment.

    PVC naming: pvc-{container_name}-{vol_name}
    """
    volumes = []
    volume_mounts = []

    for i, (vol_name, vol_spec) in enumerate(vol_dict.items()):
        vol_k8s_name = f"vol-{container_name}-{i}"
        pvc_name = f"pvc-{container_name}-{vol_name}"

        # Volume definition (references PVC)
        volumes.append(
            f"- name: {vol_k8s_name}\n"
            f"  persistentVolumeClaim:\n"
            f"    claimName: {pvc_name}"
        )

        # VolumeMount definition
        volume_mounts.append(
            f"- name: {vol_k8s_name}\n"
            f"  mountPath: \"{vol_spec['bind']}\""
        )

    volumes_yaml = "\n".join(volumes)
    volume_mounts_yaml = "\n".join(volume_mounts)

    return volumes_yaml, volume_mounts_yaml


def generate_pvc_manifests(
    container_name: str,
    vol_dict: Dict,
    namespace: str = "default",
    storage_class: str = "gp3",
    storage_size: str = "10Gi"
) -> List[Dict]:
    """
    Generate PVC manifest dictionaries for EBS volumes.

    Returns:
        List of PVC manifests ready to be applied via kubectl
    """
    pvcs = []

    for vol_name, vol_spec in vol_dict.items():
        pvc_name = f"pvc-{container_name}-{vol_name}"

        pvc_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": pvc_name,
                "namespace": namespace,
                "labels": {
                    "app": "t4hub",
                    "container": container_name,
                    "volume": vol_name
                }
            },
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": storage_class,
                "resources": {
                    "requests": {
                        "storage": storage_size
                    }
                }
            }
        }

        pvcs.append(pvc_manifest)

    return pvcs


def ensure_local_storage_directories(
    container_name: str,
    vol_dict: Dict,
    base_path: str = "/tmp/t4hub-storage"
) -> None:
    """
    Create local directories for hostPath storage before pod creation.

    This is only needed for hostPath type. For NFS/EBS, directories/volumes
    are managed externally.
    """
    import os

    for vol_name in vol_dict.keys():
        dir_path = os.path.join(base_path, container_name, vol_name)
        try:
            os.makedirs(dir_path, mode=0o777, exist_ok=True)
            logger.info(f"Created storage directory: {dir_path}")
        except Exception as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            raise


def ensure_config_directory(
    base_path: str = "/tmp/t4hub-storage"
) -> None:
    """
    Create shared config directory for hostPath storage.
    """
    import os

    config_path = os.path.join(base_path, "config-apphub")
    try:
        os.makedirs(config_path, mode=0o777, exist_ok=True)
        logger.info(f"Created config directory: {config_path}")
    except Exception as e:
        logger.error(f"Failed to create config directory {config_path}: {e}")
        raise
