from fastapi import APIRouter, Depends, Body, Request
from sqlalchemy.orm import Session

from PE.weespas.core.database import get_db
from PE.weespas.schemas.contact import ContactRequest, ContactResponse
from PE.weespas.models.contact import ContactSubmission

router = APIRouter(prefix="/contact", tags=["Contact"])


@router.post(
    "",
    response_model=ContactResponse,
    status_code=201,
    summary="Submit a contact form inquiry",
)
def submit_contact(
    request: Request,
    data: ContactRequest = Body(...),
    db: Session = Depends(get_db),
):
    submission = ContactSubmission(
        inquiry_purpose=data.inquiry_purpose,
        description=data.description,
        full_name=data.full_name,
        email=data.email,
        organization=data.organization,
        phone=data.phone,
        message=data.message,
        property_id=data.property_id,
        ip_address=getattr(request.state, "client_ip", None),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission
