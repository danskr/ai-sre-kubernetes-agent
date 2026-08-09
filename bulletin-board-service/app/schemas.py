from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    author: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)


class MessageUpdate(BaseModel):
    author: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=10000)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author: str
    title: str
    body: str
    created_at: datetime
    updated_at: datetime


class MessageList(BaseModel):
    items: list[MessageRead]
    total: int
    limit: int
    offset: int
