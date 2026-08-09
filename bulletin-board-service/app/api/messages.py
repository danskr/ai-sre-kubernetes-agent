import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.demo_faults import DB_LEAK_FAULT
from app.models import Message
from app.schemas import MessageCreate, MessageList, MessageRead, MessageUpdate

router = APIRouter(prefix='/messages', tags=['messages'])
settings = get_settings()


def maybe_add_fault_latency() -> None:
    if settings.demo_faults_enabled:
        delay = DB_LEAK_FAULT.extra_latency_seconds()
        if delay > 0:
            time.sleep(delay)


@router.post('', response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def create_message(payload: MessageCreate, db: Session = Depends(get_db)) -> Message:
    maybe_add_fault_latency()
    message = Message(**payload.model_dump())
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get('', response_model=MessageList)
def list_messages(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    author: str | None = Query(default=None, min_length=1, max_length=100),
    db: Session = Depends(get_db),
) -> MessageList:
    maybe_add_fault_latency()
    filters = [Message.author == author] if author else []
    total = db.scalar(select(func.count(Message.id)).where(*filters)) or 0
    messages = db.scalars(
        select(Message)
        .where(*filters)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return MessageList(items=list(messages), total=total, limit=limit, offset=offset)


@router.get('/{message_id}', response_model=MessageRead)
def get_message(message_id: int, db: Session = Depends(get_db)) -> Message:
    maybe_add_fault_latency()
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Message not found')
    return message


@router.patch('/{message_id}', response_model=MessageRead)
def update_message(message_id: int, payload: MessageUpdate, db: Session = Depends(get_db)) -> Message:
    maybe_add_fault_latency()
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Message not found')
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='At least one field must be provided')
    for field, value in updates.items():
        setattr(message, field, value)
    db.commit()
    db.refresh(message)
    return message


@router.delete('/{message_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_message(message_id: int, db: Session = Depends(get_db)) -> Response:
    maybe_add_fault_latency()
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Message not found')
    db.delete(message)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
