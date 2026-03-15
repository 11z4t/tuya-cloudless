"""pytest configuration and fixtures.

crypto.py uses the 'cryptography' package (PyCA) exclusively — no pycryptodome required.
Tests run without any mocking since cryptography is a standard dependency.
"""
