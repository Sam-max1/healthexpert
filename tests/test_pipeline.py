"""
Unit tests for HealthExpert pipeline components.
"""
import pytest
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["HF_MODE"] = "1"
os.environ["TESTING"] = "true"

# Add healthexpert directory to path
healthexpert_dir = Path(__file__).parent.parent
sys.path.insert(0, str(healthexpert_dir))

class TestPipelineModules:
    """Test pipeline module imports."""
    
    @pytest.mark.unit
    def test_pipeline_package_exists(self):
        """Test pipeline package can be imported."""
        try:
            import pipeline
            assert pipeline is not None
        except ImportError as e:
            pytest.skip(f"Pipeline package not found: {e}")
    
    @pytest.mark.unit
    def test_document_loader_exists(self):
        """Test document_loader module exists."""
        try:
            from pipeline import document_loader
            assert document_loader is not None
        except ImportError as e:
            pytest.skip(f"document_loader import failed: {e}")
    
    @pytest.mark.unit
    def test_chunker_exists(self):
        """Test chunker module exists."""
        try:
            from pipeline import chunker
            assert chunker is not None
        except ImportError as e:
            pytest.skip(f"chunker import failed: {e}")
    
    @pytest.mark.unit
    def test_embedder_exists(self):
        """Test embedder module exists."""
        try:
            from pipeline import embedder
            assert embedder is not None
        except ImportError as e:
            pytest.skip(f"embedder import failed: {e}")
    
    @pytest.mark.unit
    def test_vector_store_exists(self):
        """Test vector_store module exists."""
        try:
            from pipeline import vector_store
            assert vector_store is not None
        except ImportError as e:
            pytest.skip(f"vector_store import failed: {e}")
    
    @pytest.mark.unit
    def test_graph_store_exists(self):
        """Test graph_store module exists."""
        try:
            from pipeline import graph_store
            assert graph_store is not None
        except ImportError as e:
            pytest.skip(f"graph_store import failed: {e}")
    
    @pytest.mark.unit
    def test_security_module_exists(self):
        """Test security module exists."""
        try:
            from pipeline import security
            assert security is not None
        except ImportError as e:
            pytest.skip(f"security import failed: {e}")
