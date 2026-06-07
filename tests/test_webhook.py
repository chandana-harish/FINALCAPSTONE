import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

# Import the FastAPI app
# First ensure backend is in python path
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy environment variables for config class validation
os.environ["ADO_ORG_NAME"] = "test-org"
os.environ["ADO_PAT"] = "test-pat"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["OPENAI_MODEL_NAME"] = "gpt-4o"
os.environ["COSMOS_URI"] = "https://test.documents.azure.com:443/"
os.environ["COSMOS_KEY"] = "test-key"

from backend.main import app
from backend.services.azure_devops import ado_client

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
    
    cleaned_log = ado_client.clean_and_truncate_log(raw_log)
    
    # Assert timestamp was stripped
    assert "2026-06-07T14:30:52.1234567Z" not in cleaned_log
    # Assert it extracted the error lines with context
    assert "npm ERR! code ELIFECYCLE" in cleaned_log
    assert "build failed with exit code 1" in cleaned_log
    assert "[EXTRACTED LOG SEGMENTS FOCUSING ON ERRORS]" in cleaned_log

def test_webhook_successful_build_ignored():
    # Payload for a successful build
    payload = {
        "eventType": "build.complete",
        "resource": {
            "id": 1234,
            "buildNumber": "20260607.1",
            "status": "succeeded",
            "project": {"name": "TestProject"},
            "definition": {"name": "TestPipeline"}
        }
    }
    
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

@patch("backend.main.process_failed_run")
def test_webhook_failed_build_queued_classic(mock_process_task):
    # Payload for a failed classic build
    payload = {
        "eventType": "build.complete",
        "resource": {
            "id": 9999,
            "buildNumber": "20260607.9",
            "result": "failed",
            "project": {"name": "TestProject"},
            "definition": {"name": "TestPipeline"}
        }
    }
    
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    
    # Assert background task was scheduled with correct parsed values
    mock_process_task.assert_called_once_with(
        project_name="TestProject",
        pipeline_name="TestPipeline",
        build_id=9999,
        run_number="20260607.9"
    )

@patch("backend.main.process_failed_run")
def test_webhook_failed_run_queued_yaml(mock_process_task):
    # Payload for a failed YAML pipeline run (state-changed)
    payload = {
        "eventType": "ms.vss-pipelines.run-state-changed-event",
        "resource": {
            "run": {
                "id": 8888,
                "name": "20260607.2",
                "state": "completed",
                "result": "failed"
            },
            "pipeline": {
                "name": "YAML-Pipeline"
            },
            "project": {
                "name": "MyProject"
            }
        }
    }
    
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    
    # Assert background task was scheduled with correct parsed values
    mock_process_task.assert_called_once_with(
        project_name="MyProject",
        pipeline_name="YAML-Pipeline",
        build_id=8888,
        run_number="20260607.2"
    )

def test_webhook_malformed_payload():
    payload = {
        "eventType": "build.complete",
        "resource": {
            # Missing run ID and project details
            "result": "failed"
        }
    }
    response = client.post("/webhook/cicd-failure", json=payload)
    assert response.status_code == 422
