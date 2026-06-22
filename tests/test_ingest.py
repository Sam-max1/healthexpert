"""
Unit tests for HealthExpert ingestion and session token handling.
"""
import pytest
import os
import sys
from pathlib import Path
from io import BytesIO
from unittest.mock import patch, MagicMock

# Setup environment
os.environ["HF_MODE"] = "1"
os.environ["ADMIN_MODE"] = "0"
os.environ["TESTING"] = "true"

# Add healthexpert directory to path
healthexpert_dir = Path(__file__).parent.parent
sys.path.insert(0, str(healthexpert_dir))

@pytest.fixture
def client():
    """Create test client with mocked pipeline/store dependencies."""
    with patch("app.vector_store") as mock_vector, \
         patch("app.graph_store") as mock_graph, \
         patch("app.embedder"), \
         patch("app.document_loader"), \
         patch("app.chunker"), \
         patch("app.trigger_kv_cache_update"):
        
        # Configure mocks to return valid data to prevent initial count/list failures
        mock_vector.count.return_value = 0
        mock_vector.list_documents.return_value = []
        mock_graph.is_available.return_value = False
        
        from app import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

@pytest.mark.unit
def test_get_session_token_multipart(client):
    """Test that get_session_token handles multipart/form-data without crashing."""
    data = {
        'files': (BytesIO(b"dummy pdf content"), 'test.pdf'),
        'session_token': 'test-session-123'
    }
    with patch("app.process_document_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {"ok": True, "result": "Ingested 1 chunks", "log": ["step 1"], "added": 1}
        resp = client.post('/api/ingest', data=data, content_type='multipart/form-data')
        
        assert resp.status_code == 200
        json_data = resp.get_json()
        assert json_data is not None
        assert "job_id" in json_data
        assert json_data["files"] == ["test.pdf"]

@pytest.mark.unit
def test_get_session_token_json(client):
    """Test get_session_token parses token correctly from headers or JSON body."""
    resp = client.get('/api/documents', headers={"X-Session-Token": "json-session-789"})
    assert resp.status_code == 200
    json_data = resp.get_json()
    assert json_data is not None
    assert "documents" in json_data
