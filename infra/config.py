"""Shared settings for the Doura deployment scripts (AWS Academy Learner Lab)."""
import os

PROJECT = "doura"
REGION = os.environ.get("AWS_REGION", "us-east-1")  # Learner Lab: us-east-1 only for this project
LAB_ROLE_NAME = "LabRole"                            # single pre-configured role, never create roles


def name(resource: str) -> str:
    """Prefix every AWS resource so it is easy to find and clean up: doura-<resource>."""
    return f"{PROJECT}-{resource}"
