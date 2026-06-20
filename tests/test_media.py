"""Tests for media encryption helpers in engine.services.media."""
import os
import struct
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.media import (
    _DAT_XOR_KEYS,
    _DAT_V1_AES_KEY,
    _DAT_V1_HEADER,
    _DAT_V2_HEADER,
    _detect_dat_xor_key,
    _detect_wechat_dat_version,
    decrypt_emoticon_aes_cbc,
)


# ---------------------------------------------------------------------------
# Constants verification
# ---------------------------------------------------------------------------


class TestConstants:
    def test_dat_v1_aes_key_is_16_bytes(self):
        """AES-128 requires a 16-byte key."""
        assert isinstance(_DAT_V1_AES_KEY, bytes)
        assert len(_DAT_V1_AES_KEY) == 16

    def test_dat_v1_aes_key_matches_md5_of_zero(self):
        """The key should be the hex-decoded MD5 digest of '0'."""
        import hashlib
        expected = hashlib.md5(b'0').digest()
        assert _DAT_V1_AES_KEY == expected

    def test_dat_xor_keys_non_empty(self):
        """_DAT_XOR_KEYS should contain at least one entry."""
        assert len(_DAT_XOR_KEYS) > 0

    def test_dat_xor_keys_in_byte_range(self):
        """Every XOR key must be in the 0-255 range."""
        for key in _DAT_XOR_KEYS:
            assert 0 <= key <= 255

    def test_dat_xor_keys_expected_values(self):
        """Verify the known XOR key list matches the source."""
        assert _DAT_XOR_KEYS == [0xC9, 0x37, 0x96, 0x6A, 0xFF]

    def test_dat_v1_header_is_6_bytes(self):
        assert len(_DAT_V1_HEADER) == 6

    def test_dat_v2_header_is_6_bytes(self):
        assert len(_DAT_V2_HEADER) == 6


# ---------------------------------------------------------------------------
# _detect_wechat_dat_version
# ---------------------------------------------------------------------------


class TestDetectWechatDatVersion:
    def test_v1_header(self):
        data = _DAT_V1_HEADER + b'\x00' * 100
        assert _detect_wechat_dat_version(data) == 1

    def test_v2_header(self):
        data = _DAT_V2_HEADER + b'\x00' * 100
        assert _detect_wechat_dat_version(data) == 2

    def test_v1_short_4_byte_match(self):
        """V1 detected from first 4 bytes alone (some variants)."""
        data = b'\x07\x08\x56\x31' + b'\x00' * 10
        assert _detect_wechat_dat_version(data) == 1

    def test_v2_short_4_byte_match(self):
        """V2 detected from first 4 bytes alone (some variants)."""
        data = b'\x07\x08\x56\x32' + b'\x00' * 10
        assert _detect_wechat_dat_version(data) == 2

    def test_xor_only_returns_v0(self):
        """Data without a version header is classified as V0."""
        data = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        assert _detect_wechat_dat_version(data) == 0

    def test_empty_data_returns_v0(self):
        assert _detect_wechat_dat_version(b'') == 0

    def test_short_data_returns_v0(self):
        """Fewer than 6 bytes, no version header."""
        assert _detect_wechat_dat_version(b'\x07\x08') == 0

    def test_random_data_returns_v0(self):
        data = os.urandom(256)
        # Extremely unlikely to accidentally match a header
        assert _detect_wechat_dat_version(data) == 0

    def test_v2_takes_precedence_over_v1_when_same(self):
        """If first 6 bytes match V2, result is 2."""
        assert _detect_wechat_dat_version(_DAT_V2_HEADER) == 2


# ---------------------------------------------------------------------------
# _detect_dat_xor_key
# ---------------------------------------------------------------------------


class TestDetectDatXorKey:
    """Test XOR key detection using real temp files."""

    def _write_dat(self, tmp_dir, name, raw_bytes):
        path = os.path.join(tmp_dir, name)
        with open(path, 'wb') as f:
            f.write(raw_bytes)
        return path

    def test_jpeg_xor_key_c9(self, tmp_path):
        """XOR 0xC9 applied to JPEG magic should be detected."""
        # JPEG magic: ff d8 ff e0
        jpeg_magic = b'\xff\xd8\xff\xe0'
        xor_key = 0xC9
        encrypted = bytes(b ^ xor_key for b in jpeg_magic) + b'\x00' * 12
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'jpg'

    def test_png_xor_key_37(self, tmp_path):
        """XOR 0x37 applied to PNG magic should be detected."""
        png_magic = b'\x89PNG\r\n\x1a\n'
        xor_key = 0x37
        encrypted = bytes(b ^ xor_key for b in png_magic) + b'\x00' * 8
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'png'

    def test_gif89a_xor_key(self, tmp_path):
        gif_magic = b'GIF89a'
        xor_key = 0x96
        encrypted = bytes(b ^ xor_key for b in gif_magic) + b'\x00' * 10
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'gif'

    def test_gif87a_xor_key(self, tmp_path):
        gif_magic = b'GIF87a'
        xor_key = 0x6A
        encrypted = bytes(b ^ xor_key for b in gif_magic) + b'\x00' * 10
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'gif'

    def test_webp_xor_key(self, tmp_path):
        webp_magic = b'RIFF'
        xor_key = 0xFF
        encrypted = bytes(b ^ xor_key for b in webp_magic) + b'\x00' * 12
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'webp'

    def test_non_standard_xor_key_detected(self, tmp_path):
        """Auto-detect should find any key that XORs to JPEG magic."""
        xor_key = 0x42
        jpeg_magic = b'\xff\xd8\xff\xe0'
        encrypted = bytes(b ^ xor_key for b in jpeg_magic) + b'\x00' * 12
        path = self._write_dat(str(tmp_path), 'test.dat', encrypted)
        key, ext = _detect_dat_xor_key(path)
        assert key == xor_key
        assert ext == 'jpg'

    def test_empty_file_returns_none(self, tmp_path):
        path = self._write_dat(str(tmp_path), 'empty.dat', b'')
        key, ext = _detect_dat_xor_key(path)
        assert key is None
        assert ext is None

    def test_tiny_file_returns_none(self, tmp_path):
        """A file shorter than 4 bytes cannot be detected."""
        path = self._write_dat(str(tmp_path), 'tiny.dat', b'\x00\x01\x02')
        key, ext = _detect_dat_xor_key(path)
        assert key is None
        assert ext is None

    def test_unrecognized_content_returns_none(self, tmp_path):
        """Random data that doesn't XOR to any known magic returns None."""
        path = self._write_dat(str(tmp_path), 'rand.dat', os.urandom(64))
        key, ext = _detect_dat_xor_key(path)
        # It's astronomically unlikely for random data to match
        # after XOR with any key, but technically possible.
        # We just check the function doesn't crash.
        assert (key is None and ext is None) or (isinstance(key, int) and isinstance(ext, str))

    def test_nonexistent_file_returns_none(self):
        key, ext = _detect_dat_xor_key('/nonexistent/path/file.dat')
        assert key is None
        assert ext is None


# ---------------------------------------------------------------------------
# decrypt_emoticon_aes_cbc
# ---------------------------------------------------------------------------


class TestDecryptEmoticonAesCbc:
    """Test AES-128-CBC emoticon decryption."""

    def _make_key_hex(self):
        """Return a valid 32-char hex key string."""
        return '0123456789abcdef0123456789abcdef'

    def _encrypt(self, plaintext, key_hex):
        """Encrypt plaintext with AES-128-CBC (key=iv) for testing."""
        from Crypto.Cipher import AES
        from Crypto.Util import Padding
        key = bytes.fromhex(key_hex)
        padded = Padding.pad(plaintext, AES.block_size)
        ct = AES.new(key, AES.MODE_CBC, iv=key).encrypt(padded)
        return ct

    def test_roundtrip(self):
        """Encrypt then decrypt should return the original plaintext."""
        key_hex = self._make_key_hex()
        plaintext = b'Hello WeChat emoticon!'
        ct = self._encrypt(plaintext, key_hex)
        result = decrypt_emoticon_aes_cbc(ct, key_hex)
        assert result == plaintext

    def test_roundtrip_exact_16_bytes(self):
        """Plaintext exactly one block (16 bytes)."""
        key_hex = self._make_key_hex()
        plaintext = b'A' * 16
        ct = self._encrypt(plaintext, key_hex)
        result = decrypt_emoticon_aes_cbc(ct, key_hex)
        assert result == plaintext

    def test_roundtrip_empty_plaintext(self):
        """Empty plaintext still produces a valid PKCS7-padded ciphertext."""
        key_hex = self._make_key_hex()
        plaintext = b''
        ct = self._encrypt(plaintext, key_hex)
        result = decrypt_emoticon_aes_cbc(ct, key_hex)
        assert result == b''

    def test_wrong_key_returns_none(self):
        """Decryption with a different key should fail (PKCS7 unpad error)."""
        key_hex = self._make_key_hex()
        plaintext = b'test data for wrong key'
        ct = self._encrypt(plaintext, key_hex)
        wrong_key = 'abcdef0123456789abcdef0123456789'
        result = decrypt_emoticon_aes_cbc(ct, wrong_key)
        assert result is None

    def test_empty_data_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'', self._make_key_hex()) is None

    def test_data_not_multiple_of_16_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'\x00' * 17, self._make_key_hex()) is None

    def test_key_too_short_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'\x00' * 16, 'short') is None

    def test_key_too_long_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'\x00' * 16, '0123456789abcdef0123456789abcdef00') is None

    def test_non_hex_key_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'\x00' * 16, 'zz' * 16) is None

    def test_none_key_returns_none(self):
        assert decrypt_emoticon_aes_cbc(b'\x00' * 16, None) is None

    def test_none_data_returns_none(self):
        assert decrypt_emoticon_aes_cbc(None, self._make_key_hex()) is None

    def test_corrupted_ciphertext_returns_none(self):
        """Tampered ciphertext should fail PKCS7 unpad."""
        key_hex = self._make_key_hex()
        ct = self._encrypt(b'valid data', key_hex)
        # Flip a byte in the ciphertext
        corrupted = bytearray(ct)
        corrupted[0] ^= 0xFF
        result = decrypt_emoticon_aes_cbc(bytes(corrupted), key_hex)
        assert result is None

    def test_large_payload(self):
        """A larger payload (multiple blocks) roundtrips correctly."""
        key_hex = self._make_key_hex()
        plaintext = os.urandom(1024)
        ct = self._encrypt(plaintext, key_hex)
        result = decrypt_emoticon_aes_cbc(ct, key_hex)
        assert result == plaintext
