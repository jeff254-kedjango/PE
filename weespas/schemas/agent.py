from pydantic import BaseModel, Field
from typing import Optional, List


class AgentSearchResponse(BaseModel):
    id: str
    agent_name: str
    agent_phone_number: str
    agent_profile_picture: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    is_verified: bool = False
    property_count: int = 0

    class Config:
        from_attributes = True


class PaginatedAgentResponse(BaseModel):
    total: int
    skip: int
    limit: int
    items: List[AgentSearchResponse]


class AgentStatsResponse(BaseModel):
    agent_id: str
    agent_name: str
    total_properties: int = 0
    active_properties: int = 0
    inactive_properties: int = 0
    total_views: int = 0
    properties_for_sale: int = 0
    properties_for_rent: int = 0
    featured_count: int = 0
    engineer_certified_count: int = 0


class PromoteAgentRequest(BaseModel):
    agent_id: str = Field(..., description="The Agent record ID to link this user to")
