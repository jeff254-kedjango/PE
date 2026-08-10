"""Model package — importing the modules here registers every table on
``Base.metadata`` so ``create_tables()`` (Base.metadata.create_all) sees them.
Mirrors the weespas models/__init__ pattern."""
from PE.commerce.models.seller import (  # noqa: F401
    Seller, Shop, ShopSubscription, ShopSponsoredCapOverride,
)
from PE.commerce.models.listing import Listing  # noqa: F401
from PE.commerce.models.engagement import SavedListing, ListingInquiry, ListingComment, CommentLike  # noqa: F401
from PE.commerce.models.order import Order, OrderEvent, IdempotencyKey  # noqa: F401
from PE.commerce.models.receipt import Receipt  # noqa: F401
from PE.commerce.models.review import Review  # noqa: F401
from PE.commerce.models.boost import BoostGrant, BoostAllowance  # noqa: F401
from PE.commerce.models.ranking import RankingEntitlement  # noqa: F401
from PE.commerce.models.shop_view import ShopViewEvent  # noqa: F401
from PE.commerce.models.neighbourhood import Neighbourhood  # noqa: F401

__all__ = [
    "Seller",
    "Shop",
    "ShopSubscription",
    "Listing",
    "SavedListing",
    "ListingInquiry",
    "ListingComment",
    "CommentLike",
    "Order",
    "OrderEvent",
    "IdempotencyKey",
    "Receipt",
    "Review",
    "BoostGrant",
    "BoostAllowance",
    "RankingEntitlement",
    "ShopViewEvent",
    "Neighbourhood",
]
