from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class ContactRequest(BaseModel):
    inquiry_purpose: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=100)
    full_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    organization: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    message: str = Field(..., min_length=1, max_length=5000)
    property_id: Optional[str] = Field(None, max_length=64)


class ContactResponse(BaseModel):
    id: str
    inquiry_purpose: str
    description: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    organization: Optional[str] = None
    phone: Optional[str] = None
    message: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True
