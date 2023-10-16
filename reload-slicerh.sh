#!/bin/bash
echo "removing old tslicerh image.."
docker rmi localhost:5000/opendx/tslicerh:latest
echo "building new tslicerh image..."
docker build -t localhost:5000/opendx/tslicerh .
echo "push image to localhost:5000 registry..."
docker push localhost:5000/opendx/tslicerh
echo "delete old pod"
kubectl delete pod proxy-shub
# kubectl apply -f /home/administrador/3dslicerhub/3dslicerhub-deploy/tsliceh/kubernetes/tdsh_teide.yaml
