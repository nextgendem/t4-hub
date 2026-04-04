#!/bin/bash
set -e

echo "======================================="
echo "  T4-Hub K3s Remote Deployment Script"
echo "======================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration - MODIFY THESE FOR YOUR DEPLOYMENT
K3S_CONTEXT="k3s"  # Change if using different context name
STORAGE_BASE="/var/lib/t4hub-storage"
NAMESPACE="t4hub"
REMOTE_HOST=""  # Set if deploying to remote cluster (e.g., user@hostname)

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/7]${NC} Checking prerequisites..."

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites check passed${NC}"

# Step 2: Switch to k3s context
echo ""
echo -e "${YELLOW}[2/7]${NC} Switching to k3s context..."
kubectl config use-context ${K3S_CONTEXT}
echo -e "${GREEN}✓ Using context: ${K3S_CONTEXT}${NC}"

# Step 3: Create namespace
echo ""
echo -e "${YELLOW}[3/7]${NC} Creating namespace..."
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
echo -e "${GREEN}✓ Namespace ${NAMESPACE} ready${NC}"

# Step 4: Create storage directories on remote nodes
echo ""
echo -e "${YELLOW}[4/7]${NC} Creating storage directories..."

if [ -z "${REMOTE_HOST}" ]; then
    # Local deployment
    echo "Creating directories locally..."
    sudo mkdir -p ${STORAGE_BASE}/config-apphub
    sudo mkdir -p ${STORAGE_BASE}/postgres-data
    sudo chmod -R 777 ${STORAGE_BASE}
    echo -e "${GREEN}✓ Storage directories created at ${STORAGE_BASE}${NC}"
else
    # Remote deployment
    echo "Creating directories on remote host: ${REMOTE_HOST}..."
    ssh ${REMOTE_HOST} "sudo mkdir -p ${STORAGE_BASE}/config-apphub && \
                        sudo mkdir -p ${STORAGE_BASE}/postgres-data && \
                        sudo chmod -R 777 ${STORAGE_BASE}"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Storage directories created on ${REMOTE_HOST}${NC}"
    else
        echo -e "${RED}Error: Failed to create directories on remote host${NC}"
        exit 1
    fi
fi

# Step 5: Build and push images
echo ""
echo -e "${YELLOW}[5/7]${NC} Building and deploying images..."

echo "NOTE: For remote k3s, you need to either:"
echo "  1. Build images on each node, OR"
echo "  2. Use a container registry (Docker Hub, private registry, etc.)"
echo ""
echo "This script assumes images are built and available on all nodes."
echo "If using a registry, make sure to update the image references in k3s-remote.yaml"
echo ""
read -p "Press Enter to continue or Ctrl+C to cancel..."

# Step 6: Clean up old deployment (if exists)
echo ""
echo -e "${YELLOW}[6/7]${NC} Cleaning up old deployment..."
kubectl delete -f apphub/kubernetes/k3s-remote.yaml -n ${NAMESPACE} --ignore-not-found=true
echo "Waiting for resources to be deleted..."
sleep 5
echo -e "${GREEN}✓ Old deployment cleaned up${NC}"

# Step 7: Apply new deployment
echo ""
echo -e "${YELLOW}[7/7]${NC} Deploying t4-hub to k3s..."
if kubectl apply -f apphub/kubernetes/k3s-remote.yaml; then
    echo -e "${GREEN}✓ Deployment manifest applied${NC}"
else
    echo -e "${RED}Error: Failed to apply deployment manifest${NC}"
    exit 1
fi

# Wait for pods to be ready
echo ""
echo "Waiting for pods to be ready (this may take a few minutes)..."
if kubectl wait --for=condition=ready pod -l app=postgresql -n ${NAMESPACE} --timeout=300s 2>/dev/null; then
    echo -e "${GREEN}✓ PostgreSQL pod is ready${NC}"
else
    echo -e "${YELLOW}Warning: PostgreSQL pod not ready yet${NC}"
fi

if kubectl wait --for=condition=ready pod -l app=app-hub -n ${NAMESPACE} --timeout=300s 2>/dev/null; then
    echo -e "${GREEN}✓ App-hub pod is ready${NC}"
else
    echo -e "${YELLOW}Warning: App-hub pod not ready yet${NC}"
fi

# Get service access information
echo ""
echo "Getting service access information..."
SERVICE_INFO=$(kubectl get svc apphub-svc -n ${NAMESPACE} 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "${SERVICE_INFO}"

    # Check if LoadBalancer
    if echo "${SERVICE_INFO}" | grep -q "LoadBalancer"; then
        EXTERNAL_IP=$(kubectl get svc apphub-svc -n ${NAMESPACE} -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
        if [ -n "${EXTERNAL_IP}" ]; then
            echo ""
            echo -e "${GREEN}LoadBalancer IP: ${EXTERNAL_IP}${NC}"
            echo "Access t4-hub at: http://${EXTERNAL_IP}"
        else
            echo ""
            echo -e "${YELLOW}LoadBalancer IP pending...${NC}"
            echo "Run 'kubectl get svc apphub-svc -n ${NAMESPACE}' to check status"
        fi
    fi
fi

# Summary
echo ""
echo "======================================"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "======================================"
echo ""
echo "Namespace: ${NAMESPACE}"
echo "Storage: ${STORAGE_BASE}"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n ${NAMESPACE}                                # Check pod status"
echo "  kubectl logs -f proxy-app-hub -c app-hub -n ${NAMESPACE}        # View app-hub logs"
echo "  kubectl logs -f proxy-app-hub -c nginx-container -n ${NAMESPACE}# View nginx logs"
echo "  kubectl exec -ti proxy-app-hub -c app-hub -n ${NAMESPACE} -- bash  # Shell into app-hub"
echo "  kubectl get svc -n ${NAMESPACE}                                 # Check service status"
echo ""
echo "To delete deployment:"
echo "  kubectl delete namespace ${NAMESPACE}"
echo ""
