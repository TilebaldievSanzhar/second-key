"""Приём выгрузок: файл в хранилище, предпросмотр шапки и сопоставления колонок."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status

from secondkey.api.deps import UPLOAD, CurrentMember, Member, Session, require
from secondkey.api.schemas import UploadOut
from secondkey.config import settings
from secondkey.db.models import Upload
from secondkey.services.audits import UserFacingError, create_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, session: Session, member: Member = require(*UPLOAD)) -> Upload:
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Файл больше {settings.max_upload_mb} МБ — выгрузите период короче или без лишних колонок",
        )
    try:
        return await create_upload(
            session, member.org_id, member.user.id, file.filename or "upload.xlsx", file.content_type, data
        )
    except UserFacingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/{upload_id}", response_model=UploadOut)
async def get_upload(upload_id: uuid.UUID, session: Session, member: CurrentMember) -> Upload:
    up = await session.get(Upload, upload_id)
    if up is None or up.org_id != member.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    return up
