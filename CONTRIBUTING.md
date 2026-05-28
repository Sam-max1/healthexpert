# Contributing to HealthExpert

Thank you for your interest in contributing to HealthExpert! We welcome all contributions, including bug reports, feature requests, documentation improvements, and code changes.

## Code of Conduct

Please be respectful and constructive in all interactions. We are committed to providing a welcoming and inspiring community for all.

## How to Contribute

### 1. Reporting Bugs

If you find a bug, please create an issue with:

- **Clear title**: Describe the bug in one sentence
- **Description**: What you expected vs. what happened
- **Steps to reproduce**: Exact steps to reproduce the issue
- **Environment**: Python version, OS, GPU/CPU, relevant packages
- **Logs**: Error messages or stack traces

**Example:**

```
Title: KV Cache update fails on large documents

Description:
When ingesting documents larger than 50MB, the KV cache update endpoint returns a 500 error.

Steps:
1. Upload a 100MB PDF file
2. Wait for ingestion to complete
3. Observe error in logs

Error:
RuntimeError: CUDA out of memory
```

### 2. Suggesting Features

Please open an issue with:

- **Clear title**: Feature description
- **Motivation**: Why this feature would be useful
- **Proposed solution**: How it could work
- **Alternative approaches**: Other ways to solve the problem

**Example:**

```
Title: Add batch processing API

Motivation:
Users want to ingest multiple documents at once without individual API calls.

Proposed Solution:
Add POST /api/ingest/batch endpoint that accepts multiple files.

Alternative:
Queue-based approach with job orchestration.
```

### 3. Pull Requests

#### Before Starting

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/my-feature
   git checkout -b fix/issue-123
   ```

3. **Set up development environment**:
   ```bash
   python -m venv venv_dev
   source venv_dev/bin/activate
   pip install -r requirements.txt
   pip install pytest black flake8 mypy
   ```

#### During Development

1. **Write clean code**:
   - Follow PEP 8
   - Use type hints where possible
   - Add docstrings to functions/classes

2. **Add tests**:
   ```bash
   # Add tests to tests/ directory
   # Run tests
   pytest tests/ -v
   ```

3. **Format code**:
   ```bash
   black healthexpert/ agents/ pipeline/
   flake8 healthexpert/ agents/ pipeline/ --max-line-length=100
   mypy healthexpert/ agents/ pipeline/
   ```

4. **Update documentation**:
   - Update README.md if needed
   - Add docstrings
   - Update CHANGELOG

#### Before Submitting

1. **Test thoroughly**:
   ```bash
   # Run all tests
   pytest tests/ -v --cov=healthexpert

   # Test CLI
   python healthexpert.py ingest test_document.pdf
   python healthexpert.py query "Test query"
   ```

2. **Check for common issues**:
   - No hardcoded credentials
   - Proper error handling
   - Logging in place of print statements
   - No unnecessary dependencies

3. **Update documentation**:
   ```bash
   # In docstring, add clear explanation
   # Update README.md if behavior changes
   # Update CHANGELOG.md
   ```

#### Submitting PR

1. **Create Pull Request** with:
   - Clear title: "Feature: Add batch ingestion API" or "Fix: KV cache timeout"
   - Description: What changed and why
   - Link related issues: "Fixes #123"
   - Checklist completion

2. **Example PR Template**:

```markdown
## Description
Added batch ingestion endpoint for processing multiple files.

## Type of Change
- [x] New feature
- [ ] Bug fix
- [ ] Breaking change
- [ ] Documentation

## Related Issues
Fixes #45

## Testing
- [x] Added unit tests
- [x] Added integration tests
- [x] Tested locally

## Checklist
- [x] Code follows style guidelines
- [x] Self-review completed
- [x] Comments added for complex sections
- [x] Documentation updated
- [x] No new warnings generated
```

### 4. Documentation

Documentation improvements are valuable! Please:

1. Fix typos
2. Clarify ambiguous sections
3. Add examples
4. Update outdated information
5. Improve architecture diagrams

## Development Guidelines

### Code Style

```python
# Good: Type hints, docstrings, clear naming
def process_document(
    file_path: str,
    chunk_size: int = 512,
) -> dict[str, Any]:
    """Process a document and return chunks with metadata.
    
    Args:
        file_path: Path to document file
        chunk_size: Size of chunks in tokens
        
    Returns:
        Dictionary with chunks and metadata
    """
    # Implementation
    pass

# Logging instead of print
import logging
log = logging.getLogger(__name__)
log.info(f"Processing {file_path}")
log.error(f"Failed to process: {error}")
```

### Testing

```python
# tests/test_document_loader.py
import pytest
from pipeline.document_loader import load_document

def test_load_pdf():
    """Test loading a PDF file."""
    doc = load_document("test.pdf")
    assert doc is not None
    assert len(doc.pages) > 0

def test_load_nonexistent_file():
    """Test error handling for missing files."""
    with pytest.raises(FileNotFoundError):
        load_document("nonexistent.pdf")
```

### Performance

- Use async/await for I/O operations
- Batch API calls when possible
- Cache expensive computations
- Profile before optimizing

### Security

- Never commit credentials or API keys
- Use environment variables for config
- Validate user inputs
- Sanitize file uploads

## Commit Guidelines

```bash
# Clear, descriptive commits
git commit -m "Add batch ingestion endpoint

- Add POST /api/ingest/batch endpoint
- Support up to 10 files per request
- Add rate limiting
- Add integration tests"

# Avoid
git commit -m "fix stuff"
git commit -m "WIP"
git commit -m "asdfgh"
```

## Review Process

1. A maintainer will review your PR within 1-2 weeks
2. Automated tests must pass
3. Code review comments will be addressed
4. Once approved, PR will be merged
5. Your contribution will be acknowledged

## Questions?

- **Ask in Issues**: For questions about the project
- **Email**: sdas@live.com
- **Documentation**: See README.md and HEALTHEXPERT_ARCHITECTURE_DESIGN.md

## Recognition

Contributors will be:
- Added to CONTRIBUTORS.md
- Mentioned in release notes
- Credited in relevant files

Thank you for making HealthExpert better! 🚀
