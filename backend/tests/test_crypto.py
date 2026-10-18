from datetime import datetime, timezone

from src.service.crypto import CryptoService


def test_crypto_service_encryption_roundtrip():
    crypto = CryptoService()
    secret = "sk-proj-super-secret-api-key-12345"
    encrypted = crypto.encrypt(secret)
    assert encrypted != secret
    decrypted = crypto.decrypt(encrypted)
    assert decrypted == secret


def test_crypto_service_cursor_encoding_and_decoding():
    crypto = CryptoService()
    now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    item_id = "session-uuid-9876"

    cursor = crypto.encode_cursor(now, item_id)
    assert isinstance(cursor, str)
    assert len(cursor) > 0

    decoded_dt, decoded_id = crypto.decode_cursor(cursor)
    assert decoded_dt == now
    assert decoded_id == item_id


def test_crypto_service_invalid_cursor_handling():
    crypto = CryptoService()
    # Malformed / tampered cursor must return (None, None) gracefully without throwing uncaught exceptions
    dt, item_id = crypto.decode_cursor("not_a_valid_fernet_token")
    assert dt is None
    assert item_id is None
