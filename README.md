# 3dslicerhub


### Execute locally (single machine)

minikube start
cd ~/GoogleDrive/AA_OpenDx28/3dslicerhub
docker build -t opendx/tslicerh .
minikube image load opendx/tslicerh
