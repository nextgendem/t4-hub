# 3dslicerhub
**3DSlicer Hub** is an application that imitates the functionality of **JupyterHub**, but for **3DSlicer**. It allows users to efficiently manage 3DSlicer instances, providing a login mechanism, the ability to launch instances, and share them, as well as integrating with a reverse proxy for simplified access.

## Prerequisites

- Python 3.7 or higher
- Docker

A. Docker Compose Orchestrator
- Docker Compose

B. Kubernetes Orchestrator
- Minikube

## Installation

### Running with Docker Compose

1. Build the vnc version of 3D Slicer used for every session:

docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src

2. Run Docker Compose to start the environment:

docker-compose up -d

### Running with Kubernetes

## Minikube (locally)

1. **Start the Docker registry** (only once):
Registry Docker container https://hub.docker.com/_/registry is a Distribution implementation for storing and distributing
of container images and artifacts that allows to use locally built docker images in kubernetes.
   ```bash
   docker run -d -p 5000:5000 --restart=always --name registry registry:2
   
2. Build and push the 3DSlicer Hub image to the Docker registry:

     ```bash
    docker build -t localhost:5000/opendx28/tslicerh .
    docker push localhost:5000/opendx28/tslicerh
   
3. Build the vnc version of 3D Slicer and push it to the Docker registry:

     ```bash
     docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src
     docker build -t localhost:5000/opendx28/slicer --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/OpenDx28/docker-slicer.git#:src
     docker push localhost:5000/opendx28/slicer

4. Start Minikube, clean other pods or deployments, and access the Minikube Docker environment:
       minikube start
   
    ```bash
        cd /path/to/your/project/3dslicerhub
        kubectl delete -f tsliceh/kubernetes/tdsh.yaml
        kubectl delete deployments -l app=slicer
        eval $(minikube docker-env)
    
5. Unset access to the Minikube Docker environment and apply the manifests:
   
    ```bash
       eval $(minikube docker-env --unset)
       kubectl apply -f /path/to/your/project/3dslicerhub/tsliceh/kubernetes/tdsh.yaml
   
### Running in cluster private

1. Install Kubernetes in your cluster.
2. If you are going to use Docker registry repeat 1,2,3 (above) in every node.
3. Apply Manifests
   ```bash
       kubectl apply -f /path/to/your/project/3dslicerhub/tsliceh/kubernetes/teide_tdsh.yaml



note that in this case of a develop environment __imagePullPolicy__ in pod manifest has to be set to __Always__ to get 
the new image everytime were build it


## Features

- **Login:** Provides a login page connected to an LDAP server.
- **Launching 3DSlicer Instances:** Allows users to start 3DSlicer instances with specific configurations in the future.
- **Instance Management:** Ability to stop unused 3DSlicer instances.
- **Integration with a Reverse Proxy:** Provides a single entry point for users.
- **Sharing Instances:** Facilitates sharing of 3DSlicer instances among users.
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

The **Orchestrator** is a critical component of the 3DSlicer Hub that manages the lifecycle of container instances running 3DSlicer. It abstracts the details of container management, allowing the application to interact with Docker and Kubernetes seamlessly. 

### Key Features of the Orchestrator:

- **Container Lifecycle Management**: The orchestrator handles the creation, starting, stopping, and removal of container instances. This allows users to launch new 3DSlicer sessions and manage existing ones efficiently.

- **Support for Multiple Backends**: The orchestrator supports both Docker and Kubernetes as orchestration backends. This flexibility allows users to deploy the application in different environments depending on their needs and infrastructure.

- **Interface Definition**: The orchestrator is defined using an abstract class (`IContainerOrchestrator`) that outlines essential methods for container management, such as `start_container`, `stop_container`, and `get_container_status`. Concrete implementations (e.g., `DockerCompose` and `Kubernetes`) provide specific logic for each orchestration method.

- **Asynchronous Operations**: The orchestrator employs asynchronous programming patterns, particularly when starting containers and waiting for their readiness. This ensures that the application remains responsive while managing multiple containers concurrently.

- **Volume Management**: The orchestrator also manages persistent storage volumes, allowing user data to be retained across container restarts. This is crucial for maintaining the state of 3DSlicer sessions.

By encapsulating the complexities of container management, the orchestrator allows the rest of the application to focus on providing a smooth user experience and managing sessions, rather than dealing with low-level container operations.





