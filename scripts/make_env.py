"""Создаёт .env из .env.example со случайными секретами (запуск из корня репозитория)."""

import re
import secrets
from pathlib import Path

src, dst = Path(".env.example"), Path(".env")
if dst.exists():
    raise SystemExit(".env уже существует — не перезаписываю")
text = re.sub(r"=change-me[\w-]*", lambda _: "=" + secrets.token_urlsafe(32), src.read_text(encoding="utf-8"))
dst.write_text(text, encoding="utf-8")
print("создан .env со случайными секретами")
