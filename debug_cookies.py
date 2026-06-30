#!/usr/bin/env python3
"""调试 Edge Cookie 解密"""
import sqlite3, os, json, win32crypt
from base64 import b64decode

db = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies')
state_path = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State')

# 1. 读取加密密钥
with open(state_path, encoding='utf-8') as f:
    state = json.load(f)
enc = b64decode(state['os_crypt']['encrypted_key'])[5:]
key = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
print(f'Key length: {len(key)} bytes, key hex: {key.hex()[:20]}...')

# 2. 读取原始 cookie 数据
conn = sqlite3.connect(db)
conn.text_factory = bytes
c = conn.cursor()
c.execute("SELECT name, hex(value), length(value) FROM cookies WHERE host_key LIKE '%.xiaohongshu.com' LIMIT 3")
rows = c.fetchall()
conn.close()

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

for name, val_hex, val_len in rows:
    val_hex_str = val_hex.decode() if isinstance(val_hex, bytes) else val_hex
    print(f'\nCookie: {name}')
    print(f'  Raw length: {val_len} bytes')
    print(f'  Raw hex: {val_hex_str}')
    
    if val_len < 3:
        print(f'  ⚠️ Too short, skipping')
        continue
    
    raw = bytes.fromhex(val_hex_str)
    print(f'  First 3 bytes: {raw[:3]}')
    
    # Try different decryption approaches
    try:
        # v10 format
        nonce = raw[3:15]
        ct = raw[15:-16]
        tag = raw[-16:]
        print(f'  v10: nonce={nonce.hex()}, ct_len={len(ct)}, tag={tag.hex()}')
        plain = AESGCM(key).decrypt(nonce, ct + tag, None)
        print(f'  ✅ v10 decrypt: {plain.decode("utf-8")}')
    except Exception as e:
        print(f'  ❌ v10 failed: {e}')
    
    try:
        # DPAPI fallback
        plain = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
        print(f'  ✅ DPAPI decrypt: {plain.decode("utf-8")}')
    except Exception as e:
        print(f'  ❌ DPAPI failed: {e}')
