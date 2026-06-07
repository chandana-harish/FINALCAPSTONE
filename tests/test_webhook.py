import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

# Import the FastAPI app
# First ensure backend is in python path
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy environment variables for config class validation
os.environ["GITHUB_PAT"] = "test-github-pat"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["OPENAI_MODEL_NAME"] = "gpt-4o"
os.environ["COSMOS_URI"] = "https://test.documents.azure.com:443/"
os.environ["COSMOS_KEY"] = "test-key"

from backend.main import app
from backend.services.github_service import github_client

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_log_cleaning_and_truncation():
    # Test text log with timestamp prefixes
    raw_log = (
        "2026-06-07T14:30:52.1234567Z Starting build...\n"
        "2026-06-07T14:30:53.1234567Z Installing dependencies...\n"
        "2026-06-07T14:30:54.1234567Z ##[error] npm ERR! code ELIFECYCLE\n"
        "2026-06-07T14:30:54.1234567Z ##[error] npm ERR! errno 1\n"
        "2026-06-07T14:30:54.1234567Z ##[error] build failed with exit code 1\n"
    )
    
    cleaned_log = github_client.clean_and_truncate_log(raw_log)
    
    # Assert timestamp was stripped
    assert "2026-06-07T14:30:52.1234567Z" not in cleaned_log
    # Assert it extracted the error lines with context
    assert "npm ERR! code ELIFECYCLE" in cleaned_log
    assert "build failed with exit code 1" in cleaned_log
    assert "[EXTRACTED LOG SEGMENTS FOCUSING ON ERRORS]" in cleaned_log

def test_webhook_successful_run_ignored():
    # Payload for a successful GitHub workflow run
    payload = {
        "action": "completed",
        "workflow_run": {
            "id": 1234,
            "run_number": 5,
            "conclusion": "success",
            "name": "Node CI/CD"
        },
        "repository": {
            "name": "test-repo",
            "owner": {"login": "test-owner"}
        }
    }
    
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

@patch("backend.main.process_failed_run")
def test_webhook_failed_run_queued(mock_process_task):
    # Payload for a failed GitHub Actions run
    payload = {
        "action": "completed",
        "workflow_run": {
            "id": 9999,
            "run_number": 12,
            "conclusion": "failure",
            "name": "Node CI/CD"
        },
        "repository": {
            "name": "node-app",
            "owner": {"login": "chandana-harish"}
        }
    }
    
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    
    # Assert background task was scheduled with correct parsed values
    mock_process_task.assert_called_once_with(
        owner="chandana-harish",
        repo="node-app",
        pipeline_name="Node CI/CD",
        run_id=9999,
        run_number="12"
    )

def test_webhook_malformed_payload():
    payload = {
        "action": "completed",
        "workflow_run": {
            # Missing ID
            "conclusion": "failure"
        }
    }
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 422
