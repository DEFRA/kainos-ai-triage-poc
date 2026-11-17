# REQUIREMENTS:
# AWS CLI installed
# Docker installed AND running


from pathlib import Path
import os
import subprocess


project_root = Path(__file__).resolve().parent
if not (project_root / "Dockerfile").is_file():
    raise RuntimeError("Place this script in kainos-ai-triage-poc where the Dockerfile is located (at root).")

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

if [ "$ACCOUNT_ID" != "637806558918" ]; then
  echo "Unexpected AWS account: $ACCOUNT_ID"
  exit 1
fi

REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
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