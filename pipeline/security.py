import os
from cryptography.fernet import Fernet
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_fernet = None

def get_fernet():
    global _fernet
    if _fernet is None:
        key_file = Path(config.ENCRYPTION_KEY_FILE)
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            key_file.write_bytes(key)
            os.chmod(key_file, 0o600)
        else:
            key = key_file.read_bytes()
        _fernet = Fernet(key)
    return _fernet

def encrypt_data(text: str) -> str:
    if not text:
        return text
    return get_fernet().encrypt(text.encode('utf-8')).decode('utf-8')

def decrypt_data(ciphertext: str) -> str:
    if not ciphertext:
        return ciphertext
    try:
        return get_fernet().decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except Exception:
        return ciphertext  # Return original if decryption fails (e.g. unencrypted data)

