#!/bin/bash
set -e

echo "======================================"
echo "  T4-Hub K3s Local Deployment Script"
echo "======================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
STORAGE_BASE="/tmp/t4hub-storage"
K3S_CONTEXT="k3s"

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/8]${NC} Checking prerequisites..."

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: docker not found. Please install docker.${NC}"
    exit 1
fi

if ! command -v k3s &> /dev/null; then
    echo -e "${RED}Error: k3s not found. Please install k3s.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"

# Step 2: Switch to k3s context
echo ""
echo -e "${YELLOW}[2/8]${NC} Switching to k3s context..."
kubectl config use-context ${K3S_CONTEXT}
echo -e "${GREEN}✓ Using context: ${K3S_CONTEXT}${NC}"

# Step 3: Create storage directories
echo ""
echo -e "${YELLOW}[3/8]${NC} Creating storage directories..."
mkdir -p ${STORAGE_BASE}/config-apphub
mkdir -p ${STORAGE_BASE}/postgres-data
mkdir -p ${STORAGE_BASE}/srv
chmod -R 777 ${STORAGE_BASE}
echo -e "${GREEN}✓ Storage directories created at ${STORAGE_BASE}${NC}"

# Step 4: Check/Build app-hub image
echo ""
echo -e "${YELLOW}[4/8]${NC} Checking app-hub image..."
if docker images | grep -q "^app-hub.*latest"; then
    echo -e "${GREEN}✓ app-hub:latest image already exists${NC}"
    echo ""
    echo "To rebuild app-hub, run:"
    echo "  cd /home/rnebot/GoogleDrive/AA_ENCARGO_JB/t4-hub"
    echo "  docker build -t app-hub:latest ."
    echo "  docker save app-hub:latest | sudo k3s ctr images import -"
    echo ""
else
    echo -e "${YELLOW}app-hub:latest not found. Building...${NC}"
    if docker build -t app-hub:latest .; then
        echo -e "${GREEN}✓ app-hub image built successfully${NC}"
    else
        echo -e "${RED}Error: Failed to build app-hub image${NC}"
        exit 1
    fi
fi

# Step 5: Check/Build transformer4 image
echo ""
echo -e "${YELLOW}[5/8]${NC} Checking transformer4 image..."
if docker images | grep -q "^transformer4.*latest"; then
    echo -e "${GREEN}✓ transformer4:latest image already exists${NC}"
    echo ""
    echo "To rebuild transformer4, run:"
    echo "  # First, ensure vnc-base exists:"
    echo "  docker build -t vnc-base:latest https://github.com/OpenDx28/docker-vnc-base.git#:src"
    echo ""
    echo "  # Then build transformer4:"
    echo "  cd /home/rnebot/GoogleDrive/AA_ENCARGO_JB/t4-novnc"
    echo "  docker build -t transformer4:latest --build-arg BASE_IMAGE=\"vnc-base:latest\" ."
    echo "  docker save transformer4:latest | sudo k3s ctr images import -"
    echo ""
else
    echo -e "${YELLOW}transformer4:latest not found. Building...${NC}"

    # Check if vnc-base exists, if not build it
    if ! docker images | grep -q "^vnc-base.*latest"; then
        echo "Building vnc-base image..."
        if docker build -t vnc-base:latest https://github.com/OpenDx28/docker-vnc-base.git#:src; then
            echo -e "${GREEN}✓ vnc-base image built successfully${NC}"
        else
            echo -e "${RED}Error: Failed to build vnc-base image${NC}"
            exit 1
        fi
    else
        echo "vnc-base image already exists, skipping build"
    fi

    # Build transformer4 from local path
    echo "Building transformer4 image from /home/rnebot/GoogleDrive/AA_ENCARGO_JB/t4-novnc..."
    if docker build -t transformer4:latest --build-arg BASE_IMAGE="vnc-base:latest" /home/rnebot/GoogleDrive/AA_ENCARGO_JB/t4-novnc; then
        echo -e "${GREEN}✓ transformer4 image built successfully${NC}"
    else
        echo -e "${RED}Error: Failed to build transformer4 image${NC}"
        exit 1
    fi
fi

# Step 6: Import images to k3s
echo ""
echo -e "${YELLOW}[6/8]${NC} Importing images to k3s..."

# Import app-hub
echo "Importing app-hub:latest..."
docker save app-hub:latest | sudo k3s ctr images import -
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ app-hub image imported to k3s${NC}"
else
    echo -e "${RED}Error: Failed to import app-hub image${NC}"
    exit 1
fi

# Import transformer4
echo "Importing transformer4:latest..."
docker save transformer4:latest | sudo k3s ctr images import -
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ transformer4 image imported to k3s${NC}"
else
    echo -e "${RED}Error: Failed to import transformer4 image${NC}"
    exit 1
fi

# Step 7: Clean up old deployment (if exists)
echo ""
echo -e "${YELLOW}[7/8]${NC} Cleaning up old deployment..."
kubectl delete -f apphub/kubernetes/k3s-local.yaml --ignore-not-found=true
echo "Waiting for resources to be deleted..."
sleep 5
echo -e "${GREEN}✓ Old deployment cleaned up${NC}"

# Step 8: Apply new deployment
echo ""
echo -e "${YELLOW}[8/8]${NC} Deploying t4-hub to k3s..."
if kubectl apply -f apphub/kubernetes/k3s-local.yaml; then
    echo -e "${GREEN}✓ Deployment manifest applied${NC}"
else
    echo -e "${RED}Error: Failed to apply deployment manifest${NC}"
    exit 1
fi

# Wait for pods to be ready
echo ""
echo "Waiting for pods to be ready (this may take a few minutes)..."
if kubectl wait --for=condition=ready pod -l app=postgresql --timeout=300s 2>/dev/null; then
    echo -e "${GREEN}✓ PostgreSQL pod is ready${NC}"
else
    echo -e "${YELLOW}Warning: PostgreSQL pod not ready yet${NC}"
fi

if kubectl wait --for=condition=ready pod -l app=app-hub --timeout=300s 2>/dev/null; then
    echo -e "${GREEN}✓ App-hub pod is ready${NC}"
else
    echo -e "${YELLOW}Warning: App-hub pod not ready yet${NC}"
fi

# Summary
echo ""
echo "======================================"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "======================================"
echo ""
echo "Access t4-hub at: http://localhost:30080"
echo ""
echo "Useful commands:"
echo "  kubectl get pods                                    # Check pod status"
echo "  kubectl logs -f proxy-app-hub -c app-hub           # View app-hub logs"
echo "  kubectl logs -f proxy-app-hub -c nginx-container   # View nginx logs"
echo "  kubectl exec -ti proxy-app-hub -c app-hub -- bash  # Shell into app-hub"
echo "  kubectl port-forward pod/proxy-app-hub 8001:80     # Alternative access via port-forward"
echo ""
echo "To delete deployment:"
echo "  kubectl delete -f apphub/kubernetes/k3s-local.yaml"
echo ""
