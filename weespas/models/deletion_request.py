from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
import uuid
from PE.weespas.core.database import Base


class DeletionRequest(Base):
    """Staff requests to delete a user/agent — requires admin approval."""
    __tablename__ = "deletion_requests"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    target_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_name_snapshot = Column(String, nullable=True)
    requested_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    reason = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)  # "pending", "approved", "rejected"
    reviewed_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
