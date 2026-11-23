# REQUIREMENTS:
# AWS CLI installed
# Docker installed AND running

# Run from the project root:
# uv run python build_push_image_ecr.py


from pathlib import Path
import os
import subprocess


project_root = Path(__file__).resolve().parent
if not (project_root / "Dockerfile").is_file():
    raise RuntimeError("Place this script in kainos-ai-triage-poc where the Dockerfile is located (at root).")

try:
  subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
except FileNotFoundError as error:
  raise RuntimeError("Docker is not installed or is not on PATH.") from error
except subprocess.CalledProcessError as error:
  raise RuntimeError("Docker is not running or its daemon is not accessible.") from error

profile = input("AWS profile name: ").strip()
if not profile:
    raise ValueError("An AWS profile name is required.")

os.environ["AWS_PROFILE"] = profile

script = r"""
set -euo pipefail

REGION="eu-west-2"
REPOSITORY="ai-triage-container-repository"

echo "Checking AWS identity..."
ACCOUNT_ID=$(aws sts get-caller-identity \
  --profile "$AWS_PROFILE" --query Account --output text)

# The hardcoded account prevents accidental pushes elsewhere but must be changed for another account
if [ "$ACCOUNT_ID" != "637806558918" ]; then
  echo "Unexpected AWS account: $ACCOUNT_ID"
  exit 1
fi

REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
# If ECR tag immutability is enabled and latest already exists, the push will fail
# Pushing a new latest image does not update running ECS tasks; redeploy the service
IMAGE="$REGISTRY/$REPOSITORY:latest"

echo "Logging in to ECR..."
aws ecr get-login-password --profile "$AWS_PROFILE" --region "$REGION" |
  docker login --username AWS --password-stdin "$REGISTRY"

echo "Building image..."
docker build --platform linux/amd64 -t "$IMAGE" .

echo "Pushing image..."
docker push "$IMAGE"
"""

subprocess.run(["bash", "-c", script], cwd=project_root, check=True)