# Transformer-4 Hub
**Transformer-4 Hub** is an application that replicates the functionality of **JupyterHub**, but for **Transformer-4**. It allows users to efficiently manage Transformer-4 instances, providing a login mechanism, the ability to launch and share instances, and integration with a reverse proxy for simplified access.

Unregistered users:

user: free_user*
password: test

## Prerequisites

- Python 3.7 or higher
- Docker

A. Docker Compose Orchestrator
- Docker Compose

B. Kubernetes Orchestrator
- Minikube

## Installation

### Running with Docker Compose

Requirements:

Docker must be correctly installed

__!!! Make sure the file `t4hub_local.env` contains the variable:__
CONTAINER_ORCHESTRATOR="docker_compose"

1. Build the VNC version of Transformer-4 used for each session:

```bash
docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src
```

2. Build transformer4 build for each session (need perms, private repository):

```bash
docker build -t transformer4 --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/nextgendem/t4-novnc#:src
```
   
3. Run Docker Compose to start the environment:

```bash
docker-compose up -d
```

## Running the code locally - Docker Compose (for Debugging)

__!!! Make sure the `.env` file contains the variable:__
CONTAINER_ORCHESTRATOR="docker_compose"

In the `.env` file, change the project path `SCRIPT_DIR="/path/to/your/project/t4-hub"` to the local path to the project.

Run Docker Compose, but only the proxy and openldap services, executing:

```bash
docker compose up -d openldap proxy
```

Run `main.py` in debugging mode.

The service will be available at `localhost:8000`.

To access the session without LDAP, use any user starting with __free_user__ and password __test__.

### Running with Kubernetes

T4-Hub supports multiple Kubernetes deployment scenarios with flexible storage backends. The deployment is automated via helper scripts.

#### Storage Backends

The storage abstraction module (`apphub/storage.py`) supports three storage types:

- **hostPath**: Local directories on k8s nodes (k3s local/remote)
- **nfs**: NFS-mounted shared storage (production clusters)
- **ebs**: AWS EBS PersistentVolumeClaims (EKS)

Configure storage via environment variables in your `.env` file:
```bash
STORAGE_TYPE="hostPath"  # or "nfs" or "ebs"
STORAGE_BASE_PATH="/tmp/t4hub-storage"  # Base directory
```

#### K3s Local Deployment (Testing)

Deploy to a local k3s cluster for development and testing.

**Prerequisites:**
- k3s installed and running
- Docker installed
- kubectl configured with k3s context

**Quick Start:**

```bash
# Run automated deployment script
./deploy-k3s-local.sh
```

This script will:
1. Build `app-hub:latest` and `transformer4:latest` images
2. Import images into k3s
3. Create storage directories at `/tmp/t4hub-storage`
4. Deploy PostgreSQL and app-hub pods
5. Expose service via NodePort 30080

**Access:** http://localhost:30080

**Manual Deployment:**

```bash
# 1. Switch to k3s context
kubectl config use-context k3s

# 2. Create storage directories
mkdir -p /tmp/t4hub-storage/config-apphub
chmod 777 /tmp/t4hub-storage

# 3. Build and import images
docker build -t app-hub:latest .
docker build -t vnc-base:latest https://github.com/OpenDx28/docker-vnc-base.git#:src
docker build -t transformer4:latest --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/nextgendem/t4-novnc#:src

docker save app-hub:latest | sudo k3s ctr images import -
docker save transformer4:latest | sudo k3s ctr images import -

# 4. Deploy
kubectl apply -f apphub/kubernetes/k3s-local.yaml

# 5. Check status
kubectl get pods
kubectl logs -f proxy-app-hub -c app-hub
```

#### K3s Remote Deployment (Production-like)

Deploy to a remote k3s cluster with persistent storage and namespace isolation.

**Prerequisites:**
- k3s cluster accessible via kubectl
- Images available on all nodes or via container registry

**Quick Start:**

```bash
# Edit script to configure remote host and storage path
# Then run:
./deploy-k3s-remote.sh
```

**Configuration:**
- Namespace: `t4hub`
- Storage: `/var/lib/t4hub-storage` (persistent across reboots)
- Service: LoadBalancer or configure Ingress
- Resource limits: Production-appropriate

**Manual Deployment:**

```bash
kubectl config use-context k3s
kubectl create namespace t4hub
kubectl apply -f apphub/kubernetes/k3s-remote.yaml
```

#### AWS EKS Deployment (Production)

Deploy to AWS EKS with EBS-backed storage and ALB ingress.

**Prerequisites:**
- EKS cluster with kubectl configured
- [AWS EBS CSI Driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html) installed
- [AWS Load Balancer Controller](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html) installed
- AWS CLI configured
- Amazon ECR for container images

**Quick Start:**

```bash
# Edit deploy-eks.sh to configure:
#   - AWS_REGION
#   - AWS_ACCOUNT_ID
#   - ECR repository names
# Then run:
./deploy-eks.sh
```

This script will:
1. Build images locally
2. Push images to Amazon ECR
3. Create namespace `t4hub-prod`
4. Deploy with EBS-backed PersistentVolumeClaims
5. Create ALB Ingress for external access

**Manual Deployment:**

```bash
# 1. Build and push images to ECR
aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.eu-west-1.amazonaws.com

docker build -t <account>.dkr.ecr.eu-west-1.amazonaws.com/t4hub/app-hub:latest .
docker push <account>.dkr.ecr.eu-west-1.amazonaws.com/t4hub/app-hub:latest

# 2. Deploy to EKS
kubectl config use-context eks
kubectl create namespace t4hub-prod
kubectl apply -f apphub/kubernetes/eks.yaml

# 3. Get Ingress URL
kubectl get ingress apphub-ingress -n t4hub-prod
```

**Important:** Update secrets and configuration in `eks.yaml` before production use:
- PostgreSQL password in `postgresql-secret`
- Database connection string in `app-hub-secret`
- Ingress hostname and ACM certificate ARN
- OAuth callback URLs

#### Legacy Minikube Deployment

For compatibility with older setups using Minikube and local Docker registry:

```bash
# Start Docker registry
docker run -d -p 5000:5000 --restart=always --name registry registry:2

# Build and push images
docker build -t localhost:5000/opendx28/tslicerh .
docker push localhost:5000/opendx28/tslicerh

docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src
docker build -t localhost:5000/opendx28/slicer --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/nextgendem/t4-novnc.git#:src
docker push localhost:5000/opendx28/slicer

# Deploy
minikube start
kubectl apply -f apphub/kubernetes/tdsh-old.yaml
minikube service my-service --url
```

#### Useful Kubectl Commands

```bash
# Check pod status
kubectl get pods -n <namespace>

# View logs
kubectl logs -f proxy-app-hub -c app-hub -n <namespace>
kubectl logs -f proxy-app-hub -c nginx-container -n <namespace>

# Shell into container
kubectl exec -ti proxy-app-hub -c app-hub -n <namespace> -- bash

# Port forwarding (alternative access)
kubectl port-forward pod/proxy-app-hub 8001:80 -n <namespace>

# Check deployments created by app-hub
kubectl get deployments -l app=t4 -n <namespace>

# Debug storage
kubectl get pvc -n <namespace>

# Delete deployment
kubectl delete -f apphub/kubernetes/<manifest>.yaml
```

## Features

- **Login:** Provides a login page connected to an LDAP server.
- **Launching Transformer-4 Instances:** Allows users to start Transformer-4 instances with specific configurations in the future.
- **Instance Management:** Ability to stop unused Transformer-4 instances.
- **Integration with a Reverse Proxy:** Provides a single entry point for users.
- **Sharing Instances:** Facilitates sharing of Transformer-4 instances among users.
- **Persistent Storage:** Offers persistent storage for new container instances.

## Documentation

For more information about the libraries used, check:

- [docker-py](https://docker-py.readthedocs.io/en/stable/)
- [FastAPI](https://fastapi.tiangolo.com/)

## Contributions

Contributions are welcome. If you wish to collaborate, please open an **issue** or a **pull request**.

## License

This project is licensed under the MIT License. For more details, please refer to the `LICENSE` file.

# Code Organization:

## Class Orchestrator

The **Orchestrator** is a critical component of the Transformer-4 Hub that manages the lifecycle of container instances running Transformer-4. It abstracts the details of container management, allowing the application to interact with Docker and Kubernetes seamlessly.

### Key Features of the Orchestrator:

- **Container Lifecycle Management**: The orchestrator handles the creation, starting, stopping, and removal of container instances. This allows users to launch new Transformer-4 sessions and manage existing ones efficiently.

- **Support for Multiple Backends**: The orchestrator supports both Docker and Kubernetes as orchestration backends. This flexibility allows users to deploy the application in different environments depending on their needs and infrastructure.

- **Interface Definition**: The orchestrator is defined using an abstract class (`IContainerOrchestrator`) that outlines essential methods for container management, such as `start_container`, `stop_container`, and `get_container_status`. Concrete implementations (e.g., `DockerCompose` and `Kubernetes`) provide specific logic for each orchestration method.

- **Asynchronous Operations**: The orchestrator employs asynchronous programming patterns, particularly when starting containers and waiting for their readiness. This ensures that the application remains responsive while managing multiple containers concurrently.

- **Volume Management**: The orchestrator also manages persistent storage volumes, allowing user data to be retained across container restarts. This is crucial for maintaining the state of Transformer-4 sessions.

By encapsulating the complexities of container management, the orchestrator allows the rest of the application to focus on providing a smooth user experience and managing sessions, rather than dealing with low-level container operations.

