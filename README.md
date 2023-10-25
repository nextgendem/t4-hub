# 3dslicerhub


### Excute using registry Docker container (mandatory in kubernetes)
Registry Docker container https://hub.docker.com/_/registry is a Distribution implementation for storing and distributing
of container images and artifacts that allows to use locally built docker images in kubernetes.

start registry: (only once)

docker run -d -p 5000:5000 --restart=always --name registry registry:2

build image and push:

docker build -t localhost:5000/opendx28/tslicerh . 
docker push localhost:5000/opendx28/tslicerh

note that in this case of a develop environment __imagePullPolicy__ in pod manifest has to be set to __Always__ to get 
the new image everytime were build it


### Execute locally (single machine)


1. Start minikube, clean other pods or deployments and access to minikube docker environment
minikube start
cd /your/project/path/3dslicerhub
kubectl delete -f tsliceh/kubernetes/tdsh.yaml
kubectl delete deployments -l app=slicer
eval $(minikube docker-env)

2. build 3DSlicer-hub and store in virtual Docker Registry
docker build -t localhost:5000/opendx28/tslicerh . (like that the image will work with registry like in production)
docker run -d -p 5000:5000 --restart=always --name registry registry:2
docker push localhost:5000/opendx28/tslicerh 

3. Build custom version od 3dSlicer and Store in virtual Docker Registry
docker run -d -p 5000:5000 --restart=always --name registry registry:2 (if it is no runned yet)
docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src
docker build -t localhost:5000/opendx28/slicer --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/OpenDx28/docker-slicer.git\#:src
docker push localhost:5000/opendx28/slicer 


4. undo access to minikube docker environment and apply manifests
eval $(minikube docker-env --unset)
kubectl apply -f /your/project/path/3dslicerhub/tsliceh/kubernetes/tdsh.yaml


### Execute in Cluster
1. build 3DSlicer-hub and store in virtual Docker Registry in every node
docker build -t localhost:5000/opendx28/tslicerh .
docker run -d -p 5000:5000 --restart=always --name registry registry:2
docker push localhost:5000/opendx28/tslicerh 

2. Build custom version od 3dSlicer and Store in virtual Docker Registry in every node
docker run -d -p 5000:5000 --restart=always --name registry registry:2 (if it is no runned yet)
docker build -t vnc-base https://github.com/OpenDx28/docker-vnc-base.git#:src
docker build -t localhost:5000/opendx28/slicer --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/OpenDx28/docker-slicer.git\#:src
docker push localhost:5000/opendx28/slicer 

3. apply manifests
kubectl apply -f /your/project/path/3dslicerhub/tsliceh/kubernetes/teide_tdsh.yaml








