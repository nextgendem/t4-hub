# 3dslicerhub


### Execute locally (single machine)

minikube start
cd ~/GoogleDrive/AA_OpenDx28/3dslicerhub
docker build -t opendx/tslicerh .
minikube image load opendx/tslicerh


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
