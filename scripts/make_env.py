"""Создаёт .env из .env.example со случайными секретами (запуск из корня репозитория)."""

import base64
import os
import re
import secrets
from pathlib import Path

src, dst = Path(".env.example"), Path(".env")
if dst.exists():
    raise SystemExit(".env уже существует — не перезаписываю")
text = re.sub(r"=change-me[\w-]*", lambda _: "=" + secrets.token_urlsafe(32), src.read_text(encoding="utf-8"))
# ключ шифрования учётных данных подключений: формат Fernet (32 байта, urlsafe base64)
text = re.sub(r"^SK_DATA_KEY=$", "SK_DATA_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode(), text, flags=re.M)
dst.write_text(text, encoding="utf-8")
print("создан .env со случайными секретами")
