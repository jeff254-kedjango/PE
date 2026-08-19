from pydantic import BaseModel, Field, HttpUrl, validator
from datetime import datetime
from typing import Optional, List
from enum import Enum


class PropertyListingType(str, Enum):
    """Listing type for validation"""
    RENT = "rent"
    SALE = "sale"


class PropertyCategory(str, Enum):
    """Property categories"""
    HOUSE = "house"
    APARTMENT = "apartment"
    VILLA = "villa"
    STUDIO = "studio"
    OFFICE = "office"
    LAND = "land"
    WAREHOUSE = "warehouse"
    SHOP = "shop"
    KIOSK = "kiosk"
    CONTAINER = "container"
    STALL = "stall"
    COMMERCIAL_SPACE = "commercial_space"
    OTHER = "other"


# ===================== PAGINATION =====================
class PaginationParams(BaseModel):
    """Pagination parameters for all list endpoints"""
    skip: int = Field(0, ge=0, description="Number of records to skip")
    limit: int = Field(20, ge=1, le=100, description="Number of records per page (max 100)")


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper"""
    total: int
    skip: int
    limit: int
    items: List[dict]


# ===================== AGENT =====================
class AgentBase(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=255)
    agent_phone_number: str = Field(...)
    agent_profile_picture: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None

    @validator('agent_phone_number')
    def validate_phone(cls, v):
        """Basic phone validation - can be extended"""
        if len(v) < 10:
            raise ValueError('Phone number must be at least 10 digits')
        return v


class AgentCreate(AgentBase):
    pass


class AgentResponse(AgentBase):
    id: str
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ===================== ADDRESS =====================
class AddressBase(BaseModel):
    location_name: str = Field(..., min_length=1, max_length=255)
    street_address: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "Kenya"
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class AddressCreate(AddressBase):
    pass


class AddressResponse(AddressBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ===================== MEDIA =====================
class PropertyImageBase(BaseModel):
    url: str
    thumbnail_url: Optional[str] = None
    alt_text: Optional[str] = None
    order: int = 0
    is_main: bool = False


class PropertyImageCreate(PropertyImageBase):
    pass


class PropertyImageResponse(PropertyImageBase):
    id: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PropertyVideoBase(BaseModel):
    url: str
    thumbnail_url: Optional[str] = None
    streaming_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[int] = None
    order: int = 0


class PropertyVideoCreate(PropertyVideoBase):
    pass


class PropertyVideoResponse(PropertyVideoBase):
    id: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ===================== PROPERTY =====================
class PropertyBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    price: float = Field(..., gt=0)
    currency: str = "KES"
    listing_type: PropertyListingType
    category: PropertyCategory
    is_engineer_certified: bool = False
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    size: Optional[str] = None
    size_numeric: Optional[float] = None
    parking_spaces: Optional[int] = None
    year_built: Optional[int] = None


class PropertyCreateRequest(PropertyBase):
    """Request model for creating a property"""
    agent_id: Optional[str] = None
    location_name: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    
    @validator('price')
    def validate_price(cls, v):
        if v < 0:
            raise ValueError('Price cannot be negative')
        return v


class PropertyResponse(PropertyBase):
    """Response model for property details"""
    id: str
    address: AddressResponse
    agent: Optional[AgentResponse] = None
    images: List[PropertyImageResponse] = []
    videos: List[PropertyVideoResponse] = []
    is_active: bool
    is_featured: bool
    view_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    featured_expires_at: Optional[datetime] = None
    distance: Optional[float] = None  # For nearby searches
    # InSAR footprint-verification state: pending | monitored | not_monitored |
    # unavailable. Lets the UI show "Verifying…" until the background task resolves.
    verification_status: str = "pending"

    class Config:
        from_attributes = True


class PropertyListResponse(BaseModel):
    """Response model for property list (lightweight)"""
    id: str
    title: str
    price: float
    currency: str
    listing_type: PropertyListingType
    category: PropertyCategory
    is_engineer_certified: bool
    location_name: str
    latitude: float
    longitude: float
    agent_name: Optional[str] = None
    main_image: Optional[PropertyImageResponse] = None
    is_featured: bool
    featured_expires_at: Optional[datetime] = None  # when the promotion lapses (None = no expiry)
    view_count: int
    created_at: datetime
    distance: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None

    class Config:
        from_attributes = True


class PaginatedPropertyResponse(BaseModel):
    """Paginated list of properties"""
    total: int
    skip: int
    limit: int
    items: List[PropertyListResponse]


# ===================== SHORTS (vertical video feed) =====================
class PropertyShortVideoEmbed(BaseModel):
    """Minimal video payload embedded in the shorts feed response."""
    url: str
    streaming_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[int] = None

    class Config:
        from_attributes = True


class PropertyShortResponse(BaseModel):
    """Lightweight property representation for the short-video feed.

    One video per property (the first by ``order``). Image fields are kept
    minimal so the payload stays small on mobile connections.
    """
    id: str
    title: str
    price: float
    currency: str
    listing_type: PropertyListingType
    category: PropertyCategory
    agent_name: Optional[str] = None
    location_name: str
    main_image: Optional[PropertyImageResponse] = None  # poster fallback
    video: PropertyShortVideoEmbed
    is_featured: bool
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None

    class Config:
        from_attributes = True


class PaginatedShortsResponse(BaseModel):
    """Paginated list of short-video listings."""
    total: int
    skip: int
    limit: int
    items: List[PropertyShortResponse]


# ===================== FILTERING =====================
class PropertyFilterParams(BaseModel):
    """Advanced filtering parameters — all fields are optional for hyper-refined search"""
    skip: int = Field(0, ge=0)
    limit: int = Field(10, ge=1, le=100)

    # Location filtering (geo-radius)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = Field(None, gt=0)  # km

    # Location filtering (text-based)
    city: Optional[str] = None
    county: Optional[str] = None
    location_name: Optional[str] = None

    # Price filtering
    min_price: Optional[float] = Field(None, ge=0)
    max_price: Optional[float] = Field(None, ge=0)

    # Category & listing type
    category: Optional[PropertyCategory] = None
    listing_type: Optional[PropertyListingType] = None

    # Attributes
    engineer_certified: Optional[bool] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    min_size: Optional[float] = Field(None, ge=0)
    max_size: Optional[float] = Field(None, ge=0)
    parking_spaces: Optional[int] = Field(None, ge=0)
    year_built: Optional[int] = Field(None, ge=1900)
    is_featured: Optional[bool] = None

    # Text search (passed by frontend as 'query')
    query: Optional[str] = None

    # Sorting
    sort_by: str = "created_at"  # created_at, price, distance
    sort_order: str = "desc"  # asc, desc

    @validator('max_price')
    def validate_price_range(cls, v, values):
        if v is not None and 'min_price' in values and values['min_price'] is not None:
            if v < values['min_price']:
                raise ValueError('max_price must be greater than min_price')
        return v

    @validator('max_size')
    def validate_size_range(cls, v, values):
        if v is not None and 'min_size' in values and values['min_size'] is not None:
            if v < values['min_size']:
                raise ValueError('max_size must be greater than min_size')
        return v


class PropertyUpdateRequest(BaseModel):
    """Request model for updating a property"""
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    is_featured: Optional[bool] = None
    featured_expires_at: Optional[datetime] = None
    is_engineer_certified: Optional[bool] = None
    is_active: Optional[bool] = None


class FeatureRequest(BaseModel):
    """Admin request to feature/unfeature a listing (the dedicated featured control).

    Precedence when featuring: explicit `featured_expires_at` > `duration_days`
    (→ now + N days) > neither (permanent feature, no expiry). Unfeaturing
    (`is_featured=False`) clears any expiry.
    """
    is_featured: bool
    duration_days: Optional[int] = Field(None, ge=1, le=365)
    featured_expires_at: Optional[datetime] = None


class RelatedPropertiesRequest(BaseModel):
    """Request model for the ranked related-properties endpoint."""
    source_ids: List[str] = Field(..., min_items=1, max_items=50)
    limit: int = Field(12, ge=1, le=30)
    exclude_ids: List[str] = Field(default_factory=list)