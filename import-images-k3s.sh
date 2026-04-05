#!/bin/bash
# Import all required images into k3s

set -e

echo "Importing images into k3s..."

# List of images to import
images=(
    "app-hub:latest"
    "transformer4:latest"
    "postgres:latest"
    "nginx:latest"
    "busybox:latest"
)

for image in "${images[@]}"; do
    echo "Importing ${image}..."
    docker save ${image} | sudo k3s ctr images import -
    if [ $? -eq 0 ]; then
        echo "✓ ${image} imported successfully"
    else
        echo "✗ Failed to import ${image}"
        exit 1
    fi
done

echo ""
echo "All images imported successfully!"
echo ""
echo "Verify with:"
echo "  sudo k3s ctr images ls | grep -E 'app-hub|transformer4|postgres|nginx|busybox'"
