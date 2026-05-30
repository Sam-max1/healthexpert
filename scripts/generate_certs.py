import os
import subprocess
from pathlib import Path

def generate_certs():
    base_dir = Path(__file__).parent.parent
    cert_path = base_dir / "cert.pem"
    key_path = base_dir / "key.pem"

    if not cert_path.exists() or not key_path.exists():
        print("Generating self-signed SSL certificate for local development...")
        try:
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096",
                "-keyout", str(key_path), "-out", str(cert_path),
                "-days", "365", "-nodes", "-subj", "/CN=localhost"
            ], check=True)
            print("SSL certificates generated successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to generate certificates: {e}")
            raise

if __name__ == "__main__":
    generate_certs()
