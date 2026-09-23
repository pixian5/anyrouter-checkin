"""Decrypt Chrome cookies on macOS for AnyRouter domain."""

import base64
import json
import os
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import tempfile

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYCHAIN_LABEL = 'Chrome Safe Storage'
ACCOUNTS = ['Chrome', 'Google Chrome']


def get_chrome_key():
	for account in ACCOUNTS:
		try:
			out = subprocess.check_output(  # nosec B603
				['/usr/bin/security', 'find-generic-password', '-w', '-a', account, '-s', KEYCHAIN_LABEL],
				text=True,
			).strip()
			if out:
				return base64.b64decode(out)
		except subprocess.CalledProcessError:
			continue
	raise RuntimeError('Failed to get Chrome key from Keychain')


def decrypt_cookie(encrypted_value, key):
	if not encrypted_value:
		return ''
	if encrypted_value.startswith(b'v10'):
		nonce = encrypted_value[3:15]
		ciphertext_with_tag = encrypted_value[15:]
		aesgcm = AESGCM(key)
		plain = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
		return plain.decode('utf-8', errors='replace')
	if encrypted_value.startswith(b'v1') or encrypted_value.startswith(b'v2'):
		rest = encrypted_value[3:]
		iv = rest[:16]
		ciphertext = rest[16:]
		cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
		dec = cipher.decryptor()
		padded = dec.update(ciphertext) + dec.finalize()
		pad_len = padded[-1]
		return padded[:-pad_len].decode('utf-8', errors='replace')
	return encrypted_value.decode('utf-8', errors='replace')


def main():
	key = get_chrome_key()
	print(f'[KEY] Decoded Chrome key length = {len(key)} bytes', file=sys.stderr)

	cookie_db = os.path.expanduser('~/Library/Application Support/Google/Chrome/Default/Cookies')
	if not os.path.exists(cookie_db):
		print(f'[ERROR] Chrome cookie DB not found at {cookie_db}', file=sys.stderr)
		sys.exit(1)

	fd, tmp_path = tempfile.mkstemp(suffix='.db')
	os.close(fd)
	shutil.copy2(cookie_db, tmp_path)

	results = {}
	conn = None
	try:
		conn = sqlite3.connect(tmp_path)
		cur = conn.cursor()
		cur.execute(
			'SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly '
			"FROM cookies WHERE host_key LIKE '%anyrouter%' OR host_key LIKE '%agentrouter%' "
			'ORDER BY host_key, name'
		)
		for host, name, value, encrypted_value, path, expires, secure, httponly in cur.fetchall():
			raw = encrypted_value if encrypted_value else (value.encode() if value else b'')
			try:
				plain = decrypt_cookie(raw, key)
			except Exception as e:
				plain = f'<DECRYPT_ERROR: {e}>'
			key_name = f'{host}::{name}'
			results[key_name] = {
				'host': host,
				'name': name,
				'value': plain,
				'path': path,
				'httponly': bool(httponly),
				'secure': bool(secure),
				'length': len(raw),
			}
	finally:
		if conn is not None:
			conn.close()
		try:
			os.unlink(tmp_path)
		except FileNotFoundError:
			pass
	print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
	main()
