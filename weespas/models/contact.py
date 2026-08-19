from sqlalchemy import Column, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
import uuid
from PE.weespas.core.database import Base


class ContactSubmission(Base):
    """Contact form submissions from the website"""
    __tablename__ = "contact_submissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    inquiry_purpose = Column(String(100), nullable=False, index=True)
    description = Column(String(100), nullable=False)
    full_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    organization = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, index=True)
    property_id = Column(String, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
