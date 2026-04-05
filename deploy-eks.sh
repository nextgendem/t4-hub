#!/bin/bash
set -e

echo "================================="
echo "  T4-Hub AWS EKS Deployment Script"
echo "================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration - MODIFY THESE FOR YOUR DEPLOYMENT
EKS_CONTEXT="eks"  # Your EKS context name
AWS_REGION="eu-west-1"  # Your AWS region
AWS_ACCOUNT_ID="474930672109"  # Your AWS account ID
ECR_REPO_BASE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
NAMESPACE="t4hub-prod"

# Image names
APP_HUB_IMAGE="${ECR_REPO_BASE}/t4hub/app-hub"
TRANSFORMER4_IMAGE="${ECR_REPO_BASE}/t4hub/transformer4"
IMAGE_TAG="latest"  # Or use git commit hash for versioning

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/10]${NC} Checking prerequisites..."

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: aws CLI not found. Please install AWS CLI.${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: docker not found. Please install docker.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"

# Step 2: Switch to EKS context
echo ""
echo -e "${YELLOW}[2/10]${NC} Switching to EKS context..."
kubectl config use-context ${EKS_CONTEXT}
echo -e "${GREEN}✓ Using context: ${EKS_CONTEXT}${NC}"

# Step 3: Verify EKS cluster access
echo ""
echo -e "${YELLOW}[3/10]${NC} Verifying EKS cluster access..."
if kubectl get nodes &> /dev/null; then
    echo -e "${GREEN}✓ EKS cluster accessible${NC}"
    kubectl get nodes
else
    echo -e "${RED}Error: Cannot access EKS cluster${NC}"
    exit 1
fi

# Step 4: Check/Install EBS CSI Driver
echo ""
echo -e "${YELLOW}[4/10]${NC} Checking EBS CSI Driver..."
if kubectl get csidriver ebs.csi.aws.com &> /dev/null; then
    echo -e "${GREEN}✓ EBS CSI Driver is installed${NC}"
else
    echo -e "${YELLOW}Warning: EBS CSI Driver not found${NC}"
    echo "Please install it following: https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 5: Build images
echo ""
echo -e "${YELLOW}[5/10]${NC} Building Docker images..."

# Build app-hub
echo "Building app-hub image..."
if docker build -t ${APP_HUB_IMAGE}:${IMAGE_TAG} .; then
    echo -e "${GREEN}✓ app-hub image built${NC}"
else
    echo -e "${RED}Error: Failed to build app-hub image${NC}"
    exit 1
fi

# Build vnc-base (if needed)
if ! docker images | grep -q vnc-base; then
    echo "Building vnc-base image..."
    docker build -t vnc-base:latest https://github.com/OpenDx28/docker-vnc-base.git#:src
fi

# Build transformer4
echo "Building transformer4 image..."
if docker build -t ${TRANSFORMER4_IMAGE}:${IMAGE_TAG} --build-arg BASE_IMAGE="vnc-base:latest" https://github.com/nextgendem/t4-novnc#:src; then
    echo -e "${GREEN}✓ transformer4 image built${NC}"
else
    echo -e "${RED}Error: Failed to build transformer4 image${NC}"
    exit 1
fi

# Step 6: Login to ECR
echo ""
echo -e "${YELLOW}[6/10]${NC} Logging in to Amazon ECR..."
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REPO_BASE}
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Logged in to ECR${NC}"
else
    echo -e "${RED}Error: Failed to login to ECR${NC}"
    exit 1
fi

# Step 7: Create ECR repositories (if they don't exist)
echo ""
echo -e "${YELLOW}[7/10]${NC} Ensuring ECR repositories exist..."

for repo in "t4hub/app-hub" "t4hub/transformer4"; do
    aws ecr describe-repositories --repository-names ${repo} --region ${AWS_REGION} &> /dev/null
    if [ $? -ne 0 ]; then
        echo "Creating repository: ${repo}"
        aws ecr create-repository --repository-name ${repo} --region ${AWS_REGION}
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ Repository ${repo} created${NC}"
        else
            echo -e "${RED}Error: Failed to create repository ${repo}${NC}"
            exit 1
        fi
    else
        echo "Repository ${repo} already exists"
    fi
done

# Step 8: Push images to ECR
echo ""
echo -e "${YELLOW}[8/10]${NC} Pushing images to ECR..."

echo "Pushing app-hub image..."
docker push ${APP_HUB_IMAGE}:${IMAGE_TAG}
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ app-hub image pushed${NC}"
else
    echo -e "${RED}Error: Failed to push app-hub image${NC}"
    exit 1
fi

echo "Pushing transformer4 image..."
docker push ${TRANSFORMER4_IMAGE}:${IMAGE_TAG}
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ transformer4 image pushed${NC}"
else
    echo -e "${RED}Error: Failed to push transformer4 image${NC}"
    exit 1
fi

# Step 9: Render kustomize overlay with updated image reference
echo ""
echo -e "${YELLOW}[9/10]${NC} Rendering kustomize overlay with image ${APP_HUB_IMAGE}:${IMAGE_TAG}..."
kubectl kustomize apphub/kubernetes/overlays/eks | \
    sed "s|474930672109.dkr.ecr.eu-west-1.amazonaws.com/t4hub/app-hub:latest|${APP_HUB_IMAGE}:${IMAGE_TAG}|g" \
    > /tmp/t4hub-eks-deploy.yaml
echo -e "${GREEN}✓ Rendered manifest written to /tmp/t4hub-eks-deploy.yaml${NC}"

# Step 10: Apply deployment
echo ""
echo -e "${YELLOW}[10/10]${NC} Deploying t4-hub to EKS..."

# Apply rendered manifest (namespace is included in the kustomize output)
if kubectl apply -f /tmp/t4hub-eks-deploy.yaml; then
    echo -e "${GREEN}✓ Deployment manifest applied${NC}"
else
    echo -e "${RED}Error: Failed to apply deployment manifest${NC}"
    exit 1
fi

# Wait for pods to be ready
echo ""
echo "Waiting for pods to be ready (this may take several minutes)..."

echo "Waiting for PostgreSQL..."
kubectl wait --for=condition=ready pod -l app=postgresql -n ${NAMESPACE} --timeout=600s 2>/dev/null || \
    echo -e "${YELLOW}Warning: PostgreSQL pod not ready yet${NC}"

echo "Waiting for App-hub..."
kubectl wait --for=condition=ready pod -l app=app-hub -n ${NAMESPACE} --timeout=600s 2>/dev/null || \
    echo -e "${YELLOW}Warning: App-hub pod not ready yet${NC}"

# Get Ingress information
echo ""
echo "Getting Ingress information..."
sleep 10  # Wait a bit for ALB to be provisioned
INGRESS_INFO=$(kubectl get ingress apphub-ingress -n ${NAMESPACE} 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "${INGRESS_INFO}"

    INGRESS_HOST=$(kubectl get ingress apphub-ingress -n ${NAMESPACE} -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
    if [ -n "${INGRESS_HOST}" ]; then
        echo ""
        echo -e "${GREEN}Ingress hostname: ${INGRESS_HOST}${NC}"
        echo ""
        echo "Note: It may take a few minutes for the ALB to become fully operational."
        echo "Update your DNS to point to this ALB hostname."
    else
        echo ""
        echo -e "${YELLOW}Ingress hostname pending...${NC}"
        echo "Run 'kubectl get ingress apphub-ingress -n ${NAMESPACE}' to check status"
    fi
fi

# Cleanup temporary file
rm -f apphub/kubernetes/eks-deploy.yaml

# Summary
echo ""
echo "======================================"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "======================================"
echo ""
echo "Namespace: ${NAMESPACE}"
echo "Images pushed to: ${ECR_REPO_BASE}/t4hub/"
echo ""
echo "Useful commands:"
echo "  kubectl get pods -n ${NAMESPACE}                                # Check pod status"
echo "  kubectl logs -f proxy-app-hub -c app-hub -n ${NAMESPACE}        # View app-hub logs"
echo "  kubectl logs -f proxy-app-hub -c nginx-container -n ${NAMESPACE}# View nginx logs"
echo "  kubectl exec -ti proxy-app-hub -c app-hub -n ${NAMESPACE} -- bash  # Shell into app-hub"
echo "  kubectl get ingress -n ${NAMESPACE}                             # Check ingress status"
echo "  kubectl get pvc -n ${NAMESPACE}                                 # Check persistent volumes"
echo ""
echo "IMPORTANT: Update the following in eks.yaml before production use:"
echo "  - PostgreSQL password in postgresql-secret"
echo "  - Database connection string in app-hub-secret"
echo "  - Ingress hostname (t4hub.yourdomain.com)"
echo "  - ACM certificate ARN (if using HTTPS)"
echo ""
echo "To delete deployment:"
echo "  kubectl delete namespace ${NAMESPACE}"
echo ""
