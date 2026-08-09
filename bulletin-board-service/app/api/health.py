from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine

router = APIRouter(prefix='/health', tags=['health'])


@router.get('/live')
def liveness() -> dict[str, str]:
    return {'status': 'alive'}


@router.get('/ready')
def readiness() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        return {'status': 'ready'}
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Database is not ready',
        ) from exc
