import pytest
from pipeline.security import encrypt_data, decrypt_data
import os

def test_encryption():
    test_str = "This is a sensitive medical document."
    encrypted = encrypt_data(test_str)
    assert encrypted != test_str
    
    decrypted = decrypt_data(encrypted)
    assert decrypted == test_str

def test_empty_encryption():
    assert encrypt_data("") == ""
    assert decrypt_data("") == ""

