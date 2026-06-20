"""Tests for engine.decrypt — key derivation, page decryption, HMAC verification."""
import hashlib
import hmac as hmac_mod
import os
import struct
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.decrypt import (
    derive_mac_key, decrypt_page, decrypt_database,
    PAGE_SZ, KEY_SZ, SALT_SZ, IV_SZ, HMAC_SZ, RESERVE_SZ, SQLITE_HDR,
)
from Crypto.Cipher import AES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encrypted_page(enc_key: bytes, mac_key: bytes, plaintext: bytes,
                         pgno: int, salt: bytes = None) -> bytes:
    """Construct a valid SQLCipher-4 encrypted page for testing.

    Layout (pgno == 1):
      [0:16]           salt
      [16:4016]        encrypted content (AES-256-CBC)
      [4016:4032]      IV
      [4032:4032]      (unused)
      [4032:4096-64]   zero padding
      [4096-64:4096]   HMAC-SHA512

    Layout (pgno > 1):
      [0:4016]         encrypted content
      [4016:4032]      IV
      [4032:4032]      (unused)
      [4032:4032]      zero padding
      [4096-64:4096]   HMAC-SHA512
    """
    iv = os.urandom(16)
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)

    if pgno == 1:
        if salt is None:
            salt = os.urandom(16)
        # plaintext for page 1: 4000 bytes (content area after salt, before IV/reserve)
        pt = plaintext[:4000].ljust(4000, b'\x00')
        encrypted = cipher.encrypt(pt)
        hmac_data = salt + encrypted + iv
    else:
        salt = b''
        # plaintext for page >1: 4016 bytes (content area before IV/reserve)
        pt = plaintext[:4016].ljust(4016, b'\x00')
        encrypted = cipher.encrypt(pt)
        hmac_data = encrypted + iv

    # Compute HMAC-SHA512
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack('<I', pgno))
    stored_hmac = hm.digest()

    # Assemble page
    page = bytearray(PAGE_SZ)
    if pgno == 1:
        page[0:SALT_SZ] = salt
        page[SALT_SZ:SALT_SZ + len(encrypted)] = encrypted
    else:
        page[0:len(encrypted)] = encrypted

    iv_offset = PAGE_SZ - RESERVE_SZ
    page[iv_offset:iv_offset + IV_SZ] = iv
    page[PAGE_SZ - HMAC_SZ:PAGE_SZ] = stored_hmac

    return bytes(page)


# ---------------------------------------------------------------------------
# derive_mac_key
# ---------------------------------------------------------------------------

class TestDeriveMacKey:
    def test_returns_32_bytes(self):
        enc_key = bytes(32)
        salt = bytes(16)
        result = derive_mac_key(enc_key, salt)
        assert isinstance(result, bytes)
        assert len(result) == KEY_SZ  # 32

    def test_deterministic(self):
        enc_key = bytes(32)
        salt = bytes(16)
        r1 = derive_mac_key(enc_key, salt)
        r2 = derive_mac_key(enc_key, salt)
        assert r1 == r2

    def test_different_key_gives_different_mac(self):
        salt = bytes(16)
        r1 = derive_mac_key(bytes(32), salt)
        r2 = derive_mac_key(b'\x01' * 32, salt)
        assert r1 != r2

    def test_different_salt_gives_different_mac(self):
        enc_key = bytes(32)
        r1 = derive_mac_key(enc_key, bytes(16))
        r2 = derive_mac_key(enc_key, b'\x01' * 16)
        assert r1 != r2

    def test_with_random_key_and_salt(self):
        """Accepts arbitrary 32-byte key and 16-byte salt."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        result = derive_mac_key(enc_key, salt)
        assert len(result) == 32


# ---------------------------------------------------------------------------
# decrypt_page
# ---------------------------------------------------------------------------

class TestDecryptPage:
    def test_page1_restores_sqlite_header(self):
        """Page 1 decryption must prepend the SQLite header."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = b'\x42' * 4000  # filler content

        page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=1, salt=salt)
        result = decrypt_page(enc_key, page, 1)

        assert len(result) == PAGE_SZ
        assert result[:16] == SQLITE_HDR

    def test_page2_returns_decrypted_content(self):
        """Page >1 decryption returns plaintext + reserve padding."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = b'\xAB' * 4016

        page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=2)
        result = decrypt_page(enc_key, page, 2)

        assert len(result) == PAGE_SZ
        # First 4016 bytes should be the decrypted content
        assert result[:4016] == plaintext[:4016]
        # Last 80 bytes should be zero padding
        assert result[4016:] == b'\x00' * RESERVE_SZ

    def test_roundtrip_with_known_plaintext(self):
        """Encrypt then decrypt produces the original plaintext."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = bytes(range(256)) * 15  # 3840 distinctive bytes

        page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=2)
        result = decrypt_page(enc_key, page, 2)
        assert result[:len(plaintext)] == plaintext

    def test_wrong_key_produces_garbage(self):
        """Decrypting with the wrong key does NOT produce the original plaintext."""
        enc_key = os.urandom(32)
        wrong_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = b'\x55' * 4016

        page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=2)
        result = decrypt_page(wrong_key, page, 2)
        assert result[:4016] != plaintext


# ---------------------------------------------------------------------------
# HMAC verification (via decrypt_database)
# ---------------------------------------------------------------------------

class TestHmacVerification:
    def _build_valid_db(self, tmp_dir: str, enc_key: bytes) -> str:
        """Build a minimal 2-page encrypted database file with valid HMACs."""
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)

        # Page 1: fake SQLite header (first 16 bytes = SQLITE_HDR, rest filler)
        p1_content = SQLITE_HDR + os.urandom(3984)
        page1 = _make_encrypted_page(enc_key, mac_key, p1_content, pgno=1, salt=salt)

        # Page 2: empty page
        p2_content = b'\x00' * 4016
        page2 = _make_encrypted_page(enc_key, mac_key, p2_content, pgno=2)

        db_path = os.path.join(tmp_dir, 'test_enc.db')
        with open(db_path, 'wb') as f:
            f.write(page1)
            f.write(page2)
        return db_path

    def test_corrupted_page1_hmac_detected(self):
        """Corrupted HMAC on page 1 must cause decrypt_database to return False."""
        with tempfile.TemporaryDirectory() as tmp:
            enc_key = os.urandom(32)
            db_path = self._build_valid_db(tmp, enc_key)
            out_path = os.path.join(tmp, 'out.db')

            # Corrupt the HMAC area of page 1 (last 64 bytes)
            with open(db_path, 'r+b') as f:
                f.seek(PAGE_SZ - HMAC_SZ)
                f.write(b'\xFF' * HMAC_SZ)

            result = decrypt_database(db_path, out_path, enc_key,
                                      print_fn=lambda *a: None)
            assert result is False

    def test_corrupted_encrypted_content_detected(self):
        """Flipping bits in encrypted content should break HMAC (content mismatch)."""
        with tempfile.TemporaryDirectory() as tmp:
            enc_key = os.urandom(32)
            db_path = self._build_valid_db(tmp, enc_key)
            out_path = os.path.join(tmp, 'out.db')

            # Flip a byte in the encrypted content area of page 1
            with open(db_path, 'r+b') as f:
                f.seek(SALT_SZ + 100)
                orig = f.read(1)
                f.seek(SALT_SZ + 100)
                f.write(bytes([orig[0] ^ 0xFF]))

            result = decrypt_database(db_path, out_path, enc_key,
                                      print_fn=lambda *a: None)
            assert result is False

    def test_file_too_small_returns_false(self):
        """A file smaller than PAGE_SZ should fail gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            small_path = os.path.join(tmp, 'small.db')
            with open(small_path, 'wb') as f:
                f.write(b'\x00' * 100)  # too small

            result = decrypt_database(small_path, os.path.join(tmp, 'out.db'),
                                      os.urandom(32),
                                      print_fn=lambda *a: None)
            assert result is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDecryptEdgeCases:
    def test_wrong_key_length_page1(self):
        """decrypt_page with a too-short key should raise or produce wrong output."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        page = _make_encrypted_page(enc_key, mac_key, b'\x00' * 4000, pgno=1, salt=salt)

        # AES with a 16-byte key is valid (AES-128), but won't match the
        # AES-256 encryption used — it should not crash but produce wrong output.
        short_key = os.urandom(16)
        result = decrypt_page(short_key, page, 1)
        assert len(result) == PAGE_SZ
        # The SQLite header is hardcoded, so first 16 bytes always match
        assert result[:16] == SQLITE_HDR
        # But the decrypted content differs
        result2 = decrypt_page(enc_key, page, 1)
        assert result != result2

    def test_page_data_exactly_minimum_size(self):
        """A page of exactly PAGE_SZ bytes should work."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        page = _make_encrypted_page(enc_key, mac_key, b'\x00' * 4016, pgno=2)
        assert len(page) == PAGE_SZ
        result = decrypt_page(enc_key, page, 2)
        assert len(result) == PAGE_SZ

    def test_all_zeros_plaintext(self):
        """Encrypting all-zeros plaintext and decrypting should roundtrip."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = b'\x00' * 4016

        page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=2)
        result = decrypt_page(enc_key, page, 2)
        assert result[:4016] == plaintext

    def test_large_page_number(self):
        """decrypt_page should work with page numbers > 1."""
        enc_key = os.urandom(32)
        salt = os.urandom(16)
        mac_key = derive_mac_key(enc_key, salt)
        plaintext = b'\x77' * 4016

        for pgno in [2, 10, 100, 1000]:
            page = _make_encrypted_page(enc_key, mac_key, plaintext, pgno=pgno)
            result = decrypt_page(enc_key, page, pgno)
            assert result[:4016] == plaintext, f"Failed for pgno={pgno}"
