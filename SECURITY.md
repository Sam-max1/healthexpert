# Security Policy

## Supported Versions

We support security updates for recent versions of HealthExpert:

| Version | Supported          |
|---------|-------------------|
| 1.0.x   | ✅ Yes            |
| < 1.0   | ❌ No             |

## Reporting Security Issues

**Do not** open public issues for security vulnerabilities. Instead:

### 1. LinkedIn Direct Message Security Report

Send a direct message on LinkedIn to [Sam-max1](https://www.linkedin.com/in/sam-max1) with:

- **Subject**: `[SECURITY] HealthExpert Vulnerability Report`
- **Description**: Clear explanation of the vulnerability
- **Steps to reproduce**: Exact steps to reproduce the issue
- **Impact**: Potential impact and severity
- **Suggested fix**: If you have a solution (optional)

### 2. What to Include

```
Title: [Brief description]
Severity: Critical/High/Medium/Low
Affected Component: [Component name]
Affected Versions: [Version range]
Discovered by: [Your name/username]

Description:
[Detailed explanation]

Steps to Reproduce:
1. [Step 1]
2. [Step 2]

Impact:
[What could an attacker do]

Suggested Fix:
[Optional]
```

### 3. Response Timeline

We commit to:

- **Acknowledge** receipt within 48 hours
- **Confirm** vulnerability within 7 days
- **Release patch** within 30 days (or sooner if critical)
- **Public disclosure** after patch is released

## Security Best Practices

### Deployment

1. **Use HTTPS**: Always use HTTPS in production
2. **Environment Variables**: Never hardcode secrets
3. **Authentication**: Implement proper auth (OAuth2, etc.)
4. **CORS**: Configure CORS properly for your domain
5. **Rate Limiting**: Implement rate limiting on APIs

### Configuration

```python
# .env (never commit this)
SECRET_KEY=generate-a-strong-secret-key
NEO4J_PASSWORD=strong-password-here
LLM_BASE_URL=https://your-domain.com:8002
```

### Database Security

```bash
# Neo4j - Change default password
neo4j-admin set-initial-password <new-password>

# ChromaDB - Store in secure location
chmod 700 data/chroma_db

# File uploads - Validate all uploads
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".xlsx", ".csv"}
```

### Input Validation

```python
# Validate file uploads
def validate_upload(file):
    # Check file size
    if len(file.read()) > MAX_FILE_SIZE:
        raise ValueError("File too large")
    
    # Check file type
    if not file.filename.endswith(ALLOWED_EXTENSIONS):
        raise ValueError("Invalid file type")
    
    # Sanitize filename
    import os
    filename = os.path.basename(file.filename)
    return filename
```

### API Security

```python
# Add CORS headers
from flask_cors import CORS
CORS(app, resources={r"/api/*": {"origins": ["https://your-domain.com"]}})

# Add rate limiting
from flask_limiter import Limiter
limiter = Limiter(app, key_func=lambda: request.remote_addr)

@app.route("/api/query", methods=["POST"])
@limiter.limit("10/minute")  # 10 requests per minute
def query():
    pass
```

## Known Security Considerations

1. **Large File Processing**: Monitor memory usage during large document ingestion
2. **LLM Output**: Always validate and sanitize LLM-generated content
3. **Database Credentials**: Use strong passwords and rotate regularly
4. **Model Caching**: Ensure model cache directory has proper permissions
5. **Network Exposure**: Don't expose services to untrusted networks

## Security Updates

- Subscribe to GitHub for release notifications
- Check [Releases](https://github.com/Sam-max1/healthexpert/releases) regularly
- Monitor dependencies with `pip install --upgrade -r requirements.txt`

## Vulnerability Scanning

We recommend using:

- `bandit`: Security linting for Python
  ```bash
  pip install bandit
  bandit -r healthexpert/ agents/ pipeline/
  ```

- `safety`: Check dependencies for vulnerabilities
  ```bash
  pip install safety
  safety check -r requirements.txt
  ```

- GitHub's Dependabot: Automatic dependency scanning

## Third-Party Dependencies

We use reputable, actively maintained libraries:

- **Flask**: Web framework - [Security](https://flask.palletsprojects.com/security/)
- **CrewAI**: Multi-agent framework
- **LangChain**: LLM orchestration
- **ChromaDB**: Vector database
- **Neo4j**: Graph database
- **Transformers**: Model library

All dependencies are regularly updated.

## Compliance

HealthExpert follows these standards:

- OWASP Top 10 mitigation strategies
- Python Security Best Practices
- NIST Cybersecurity Framework recommendations

## Questions?

- **Security Issues**: DM on LinkedIn [Sam-max1](https://www.linkedin.com/in/sam-max1)
- **General Questions**: See [README.md](README.md)

---

Thank you for helping keep HealthExpert secure! 🔒
