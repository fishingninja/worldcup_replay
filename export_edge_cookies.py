#!/usr/bin/env python3
"""从 Edge Cookie 数据库中导出指定域名的 Cookie（解密后），用于注入 Playwright。"""
import json, os, sqlite3, shutil, tempfile
import win32crypt
from base64 import b64decode

EDGE_COOKIE_DB = os.path.expandvars(
    r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies')
EDGE_LOCAL_STATE = os.path.expandvars(
    r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State')

TARGET_DOMAIN = '.xiaohongshu.com'
OUTPUT = 'cookies.json'


def get_encryption_key():
    """从 Local State 读取并解密 Edge 的加密密钥。"""
    with open(EDGE_LOCAL_STATE, 'r', encoding='utf-8') as f:
        state = json.load(f)
    encrypted_key_b64 = state['os_crypt']['encrypted_key']
    encrypted_key = b64decode(encrypted_key_b64)
    # 前 5 个字节是 "DPAPI" 标记，跳过
    encrypted_key = encrypted_key[5:]
    key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
    return key


def decrypt_value(encrypted_value, key):
    """使用 AES-GCM 解密 Edge Cookie 值。

    支持格式:
      v10/v11: 3字节标记 + 12字节 nonce + ciphertext + 16字节 tag
               直接以 DPAPI 解密出来的主密钥作为 AES 密钥
      v20 (App-Bound Encryption): 结构相同，但 AES 密钥 = SHA256(主密钥)
               （新版 Edge/Chrome 对 cookie 值改用 SHA256 派生密钥）
    """
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not encrypted_value or len(encrypted_value) < 15:
        return ''

    prefix = encrypted_value[:3]
    nonce = encrypted_value[3:15]
    ciphertext = encrypted_value[15:-16]
    tag = encrypted_value[-16:]

    # v20 (App-Bound Encryption) 与 v10/v11 结构相同，但 AES 密钥派生方式有两种可能：
    #   1) 直接以 DPAPI 解密出的主密钥作为 AES 密钥；
    #   2) SHA256(主密钥) 派生。
    # 这里两种都尝试，哪种能解出有效明文就用哪种。
    if prefix == b'v20':
        candidates = [key, hashlib.sha256(key).digest()]
    else:
        candidates = [key]

    last_err = None
    for aes_key in candidates:
        try:
            aesgcm = AESGCM(aes_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
            return plaintext.decode('utf-8')
        except Exception as e:
            last_err = e

    # 回退：尝试旧版 DPAPI 直接解密（极旧格式）
    try:
        return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode('utf-8')
    except Exception:
        return f'<decrypt_error: prefix={prefix}>'


def export_cookies(target_domain=TARGET_DOMAIN, db_path=None):
    """导出指定域名的所有 Cookie（可能含 . 前缀）。"""
    db_path = db_path or EDGE_COOKIE_DB
    if not os.path.exists(db_path):
        print(f'❌ Edge Cookie 数据库不存在: {db_path}')
        return []

    key = get_encryption_key()
    print(f'  ✅ 解密密钥获取成功 ({len(key)} bytes)', flush=True)

    # Edge 可能正在运行并锁住数据库，复制到临时文件再读取（避开文件锁）
    tmp_db = os.path.join(tempfile.gettempdir(), f'edge_cookies_{os.getpid()}.db')
    try:
        shutil.copy2(db_path, tmp_db)
    except Exception as e:
        print(f'  ⚠️ 复制数据库失败（可能被占用），直接尝试连接: {e}', flush=True)
        tmp_db = db_path

    # 连接副本（WAL 模式允许并发读）
    conn = sqlite3.connect(tmp_db)
    conn.text_factory = bytes
    cursor = conn.cursor()

    # 查询匹配域名的 Cookie（含子域名 .xiaohongshu.com 和 xiaohongshu.com）
    cursor.execute(
            'SELECT host_key, name, encrypted_value, path, is_secure, is_httponly, has_expires, expires_utc '
            'FROM cookies WHERE host_key LIKE ? OR host_key = ?',
        (f'%{target_domain}', target_domain.lstrip('.'))
    )
    rows = cursor.fetchall()
    conn.close()

    # 清理临时副本
    if tmp_db != EDGE_COOKIE_DB and os.path.exists(tmp_db):
        try:
            os.remove(tmp_db)
        except Exception:
            pass

    cookies = []
    for row in rows:
        host_key = row[0].decode('utf-8') if isinstance(row[0], bytes) else row[0]
        name = row[1].decode('utf-8') if isinstance(row[1], bytes) else row[1]
        enc_value = row[2]
        path = row[3].decode('utf-8') if isinstance(row[3], bytes) else row[3]
        is_secure = bool(row[4])
        is_httponly = bool(row[5])
        has_expires = bool(row[6])
        expires_utc = row[7]

        # 解密
        if enc_value:
            if isinstance(enc_value, bytes):
                decrypted = decrypt_value(enc_value, key)
            else:
                decrypted = enc_value
        else:
            decrypted = ''

        cookie = {
            'name': name,
            'value': decrypted,
            'domain': host_key,
            'path': path,
            'secure': is_secure,
            'httpOnly': is_httponly,
        }
        if has_expires and expires_utc:
            # Chrome 的 expires_utc 是微秒级（1601-01-01 纪元）
            cookie['expires'] = expires_utc / 1_000_000 - 11644473600
            cookie['sameSite'] = 'Lax'

        cookies.append(cookie)

    return cookies


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', help='指定 Cookie 数据库路径（默认读取 Edge）')
    args = ap.parse_args()
    cookies = export_cookies(db_path=args.db)
    if cookies:
        print(f'  ✅ 获取到 {len(cookies)} 个 Cookie（域: {TARGET_DOMAIN}）', flush=True)
        for c in cookies:
            print(f'    {c["name"]}: {c["value"][:50]}{"..." if len(c["value"]) > 50 else ""}', flush=True)
        with open(OUTPUT, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print(f'  ✅ 已保存到 {OUTPUT}', flush=True)
    else:
        print(f'  ⚠️ 未找到 {TARGET_DOMAIN} 的 Cookie', flush=True)
