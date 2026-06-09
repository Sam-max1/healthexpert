"""
Unit tests for HealthExpert configuration module.
"""
import pytest
import os
import sys
from pathlib import Path

# Add healthexpert directory to path
healthexpert_dir = Path(__file__).parent.parent
sys.path.insert(0, str(healthexpert_dir))

# Conftest for test setup
@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup test environment."""
    os.environ["HF_MODE"] = "1"
    os.environ["ADMIN_MODE"] = "0"
    os.environ["TESTING"] = "true"
    yield

# Import after environment setup
try:
    import config
except ImportError:
    # Try alternative import
    from . import sys as _sys
    _sys.path.insert(0, str(healthexpert_dir))
    import config

class TestConfiguration:
    """Test configuration module."""
    
    def test_hf_mode_enabled(self):
        """Test HF mode is correctly enabled."""
        assert config.HF_MODE == True
    
    def test_admin_mode_disabled_for_testing(self):
        """Test admin mode is disabled for security."""
        assert config.ADMIN_MODE == False
    
    def test_model_selection_hf_mode(self):
        """Test HF mode uses low-resource model."""
        if config.HF_MODE:
            assert "1.5B" in config.LLM_MODEL_ID or "small" in config.EMBEDDING_MODEL.lower()
    
    def test_upload_folder_configured(self):
        """Test upload folder is configured."""
        assert config.UPLOAD_FOLDER is not None
        assert len(config.UPLOAD_FOLDER) > 0
    
    def test_allowed_extensions_set(self):
        """Test allowed file extensions are configured."""
        required_extensions = {".pdf", ".txt", ".docx"}
        assert required_extensions.issubset(config.ALLOWED_EXTENSIONS)
    
    def test_chunk_size_valid(self):
        """Test chunk size is reasonable."""
        assert 100 <= config.CHUNK_SIZE <= 2048
    
    def test_embedding_batch_size_positive(self):
        """Test embedding batch size is positive."""
        assert config.EMBEDDING_BATCH_SIZE > 0
    
    def test_secret_key_configured(self):
        """Test secret key is configured."""
        assert config.SECRET_KEY is not None
        assert len(config.SECRET_KEY) > 0
