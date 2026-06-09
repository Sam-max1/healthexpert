"""
Pytest configuration and fixtures for HealthExpert tests.
"""
import pytest
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Setup environment
os.environ["HF_MODE"] = "1"
os.environ["ADMIN_MODE"] = "0"
os.environ["TESTING"] = "true"

# Add project root to path
project_root = Path(__file__).parent.parent / "healthexpert"
sys.path.insert(0, str(project_root))

@pytest.fixture(scope="session")
def test_dir():
    """Return test directory."""
    return Path(__file__).parent

@pytest.fixture(scope="session")
def project_dir():
    """Return project directory."""
    return Path(__file__).parent.parent / "healthexpert"

@pytest.fixture
def sample_policy_text():
    """Return sample healthcare policy text."""
    return """
    HEALTHCARE POLICY DOCUMENT
    
    1. COVERAGE OVERVIEW
    =====================
    
    Base Insurance Policy
    - Preventive Care: 100% coverage
    - Emergency Care: 80% coverage after deductible
    - Hospitalization: 90% coverage
    - Prescription Drugs: 70% coverage
    
    Super Top-up Insurance Policy
    - Additional coverage for high-cost treatments
    - Unlimited room charges
    - Covers medical tourism
    - 24/7 claims support
    
    2. ELIGIBILITY REQUIREMENTS
    ===========================
    - Full-time employees
    - Spouses and dependents
    - Retirees (age 65+)
    - Minimum employment period: 3 months
    
    3. COVERAGE LIMITS
    ==================
    - Annual limit: $1,000,000
    - Lifetime limit: None
    - Deductible: $500 (Base), $0 (Top-up)
    
    4. EXCLUSIONS
    =============
    - Cosmetic procedures
    - Experimental treatments
    - Self-inflicted injuries
    - Treatment while intoxicated
    """

@pytest.fixture
def mock_app():
    """Create mock Flask app."""
    with patch('healthexpert.app.Flask'):
        from healthexpert import app as app_module
        return app_module.app if hasattr(app_module, 'app') else MagicMock()

@pytest.fixture
def mock_vector_store():
    """Create mock vector store."""
    mock_store = MagicMock()
    mock_store.initialize_collection.return_value = True
    mock_store.add_texts.return_value = ["vec_1", "vec_2"]
    mock_store.search.return_value = [
        {"document_id": "doc_1", "text": "test", "similarity": 0.95}
    ]
    return mock_store

@pytest.fixture
def mock_llm_response():
    """Create mock LLM response."""
    return {
        "status": "success",
        "text": "Based on the healthcare policy documents, preventive care is covered at 100%.",
        "tokens": 25,
        "latency_ms": 850
    }

# Test markers
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Mark test as unit test")
    config.addinivalue_line("markers", "integration: Mark test as integration test")
    config.addinivalue_line("markers", "slow: Mark test as slow running")
    config.addinivalue_line("markers", "requires_gpu: Mark test as requiring GPU")
    config.addinivalue_line("markers", "requires_services: Mark test as requiring services")
