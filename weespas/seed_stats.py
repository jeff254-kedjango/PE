#!/usr/bin/env python3
"""
Seed script: Populate view counts, listing-type variety, and ensure agent
user accounts exist so the /agents/me/stats dashboard has meaningful data.
Safe to re-run (idempotent — updates existing rows, doesn't duplicate).
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import (
    Property, Agent, PropertyCategory, PropertyListingType,
    PropertyImage, Address,
)
from PE.weespas.models.user import User
from PE.weespas.services.auth_service import hash_password

random.seed(99)


def seed_stats():
    db = SessionLocal()

    try:
        # ── 1. Verify prerequisites exist ──
        agent_count = db.query(Agent).count()
        prop_count = db.query(Property).count()
        cat_count = db.query(PropertyCategory).count()

        if agent_count == 0 or cat_count == 0:
            print("ERROR: Run seed.py and seed_expanded.py first.")
            return

        print(f"Found {agent_count} agents, {prop_count} properties, {cat_count} categories.\n")

        # ── 2. Assign realistic view counts to all properties ──
        print("Setting view counts on existing properties...")
        properties = db.query(Property).filter(Property.is_active == True).all()
        updated_views = 0
        for prop in properties:
            # Give properties varied, realistic view counts
            # Featured properties get more views
            base = random.randint(50, 800)
            if prop.is_featured:
                base = random.randint(400, 2500)
            prop.view_count = base
            updated_views += 1

        db.commit()
        print(f"  Updated view_count on {updated_views} active properties.\n")

        # ── 3. Ensure listing type variety ──
        # Make sure agents have a mix of sale and rent properties
        print("Ensuring listing type variety across agents...")
        agents = db.query(Agent).filter(Agent.is_active == True).all()

        for agent in agents:
            agent_props = db.query(Property).filter(
                Property.agent_id == agent.id,
                Property.is_active == True,
            ).all()

            sale_count = sum(1 for p in agent_props if p.listing_type == PropertyListingType.SALE)
            rent_count = sum(1 for p in agent_props if p.listing_type == PropertyListingType.RENT)

            # If an agent has all one type, flip some for variety
            if len(agent_props) >= 3 and (sale_count == 0 or rent_count == 0):
                flip_count = max(1, len(agent_props) // 3)
                for p in agent_props[:flip_count]:
                    p.listing_type = (
                        PropertyListingType.RENT
                        if p.listing_type == PropertyListingType.SALE
                        else PropertyListingType.SALE
                    )
                print(f"  {agent.agent_name}: flipped {flip_count} listings for variety")

        db.commit()

        # ── 4. Mark some properties as featured and certified ──
        print("\nMarking featured and certified properties...")
        all_active = db.query(Property).filter(Property.is_active == True).all()
        random.shuffle(all_active)

        featured_count = 0
        certified_count = 0
        for i, prop in enumerate(all_active):
            # ~15% featured
            if i % 7 == 0 and not prop.is_featured:
                prop.is_featured = True
                prop.view_count = max(prop.view_count, random.randint(500, 3000))
                featured_count += 1
            # ~20% engineer certified
            if i % 5 == 0 and not prop.is_engineer_certified:
                prop.is_engineer_certified = True
                certified_count += 1

        db.commit()
        print(f"  Newly featured: {featured_count}, newly certified: {certified_count}\n")

        # ── 5. Add extra properties for agents with too few ──
        print("Ensuring each agent has enough properties for meaningful stats...")
        categories = {cat.slug: cat for cat in db.query(PropertyCategory).all()}

        NAIROBI_LOCATIONS = [
            ("Westlands", -1.2674, 36.8079),
            ("Kilimani", -1.2886, 36.7829),
            ("Lavington", -1.2789, 36.7714),
            ("Karen", -1.3197, 36.7091),
            ("Kileleshwa", -1.2778, 36.7767),
            ("Parklands", -1.2611, 36.8168),
            ("Runda", -1.2200, 36.8000),
            ("South B", -1.3100, 36.8400),
            ("Langata", -1.3500, 36.7500),
            ("Upperhill", -1.2950, 36.8170),
        ]

        PROPERTY_TEMPLATES = [
            ("Modern {} in {}", "house", PropertyListingType.SALE, (8_000_000, 35_000_000), (3, 5), (2, 4)),
            ("Luxury {} for Rent in {}", "apartment", PropertyListingType.RENT, (45_000, 180_000), (1, 3), (1, 2)),
            ("Spacious {} in {}", "villa", PropertyListingType.SALE, (25_000_000, 80_000_000), (4, 6), (3, 5)),
            ("Cozy {} in {}", "studio", PropertyListingType.RENT, (18_000, 45_000), (0, 0), (1, 1)),
            ("Premium {} Space in {}", "office", PropertyListingType.RENT, (60_000, 250_000), (0, 0), (1, 2)),
            ("Prime {} in {}", "land", PropertyListingType.SALE, (5_000_000, 50_000_000), (0, 0), (0, 0)),
            ("{} Retail Space in {}", "shop", PropertyListingType.RENT, (30_000, 120_000), (0, 0), (1, 1)),
        ]

        IMAGE_URLS = [
            "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=600&h=400",
            "https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=600&h=400",
            "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=600&h=400",
            "https://images.unsplash.com/photo-1613490493576-7fde63acd811?w=600&h=400",
            "https://images.unsplash.com/photo-1497366216548-37526070297c?w=600&h=400",
        ]

        added = 0
        for agent in agents:
            existing = db.query(Property).filter(Property.agent_id == agent.id).count()
            if existing >= 5:
                continue

            need = random.randint(5, 10) - existing
            print(f"  {agent.agent_name}: has {existing}, adding {need} more")

            for j in range(need):
                template = random.choice(PROPERTY_TEMPLATES)
                title_fmt, cat_slug, listing_type, price_range, beds_range, baths_range = template
                loc = random.choice(NAIROBI_LOCATIONS)
                loc_name, lat, lng = loc

                cat = categories.get(cat_slug, categories.get("other"))
                cat_display = cat.name if cat else cat_slug.title()
                title = title_fmt.format(cat_display, loc_name)

                price = random.randint(price_range[0], price_range[1])
                beds = random.randint(beds_range[0], beds_range[1]) if beds_range[1] > 0 else None
                baths = random.randint(baths_range[0], baths_range[1]) if baths_range[1] > 0 else None

                prop_id = str(uuid.uuid4())
                days_ago = random.randint(1, 180)
                created = datetime.now(timezone.utc) - timedelta(days=days_ago)

                prop = Property(
                    id=prop_id,
                    title=title,
                    description=f"Beautiful {cat_display.lower()} located in the heart of {loc_name}, Nairobi.",
                    price=price,
                    currency="KES",
                    listing_type=listing_type,
                    category_id=cat.id if cat else None,
                    agent_id=agent.id,
                    bedrooms=beds,
                    bathrooms=baths,
                    size=f"{random.randint(40, 500)} sqm",
                    size_numeric=float(random.randint(40, 500)),
                    parking_spaces=random.randint(0, 3) if cat_slug != "studio" else 0,
                    year_built=random.randint(2015, 2025) if cat_slug != "land" else None,
                    view_count=random.randint(20, 1200),
                    is_active=True,
                    is_featured=(j == 0),
                    is_engineer_certified=random.random() < 0.3,
                    created_at=created,
                    updated_at=created,
                )
                db.add(prop)
                db.flush()

                # Address
                address = Address(
                    id=str(uuid.uuid4()),
                    property_id=prop_id,
                    location_name=loc_name,
                    city="Nairobi",
                    county="Nairobi",
                    country="Kenya",
                    latitude=lat + random.uniform(-0.01, 0.01),
                    longitude=lng + random.uniform(-0.01, 0.01),
                )
                db.add(address)

                # Images (2 per property)
                img_url = random.choice(IMAGE_URLS)
                for idx in range(2):
                    db.add(PropertyImage(
                        id=str(uuid.uuid4()),
                        property_id=prop_id,
                        url=img_url,
                        thumbnail_url=img_url.replace("w=600&h=400", "w=200&h=150"),
                        alt_text=f"{title} - Image {idx+1}",
                        order=idx,
                        is_main=(idx == 0),
                    ))

                added += 1

        db.commit()
        print(f"  Total new properties added: {added}\n")

        # ── 6. Ensure all agents have linked user accounts ──
        print("Ensuring all agents have user accounts...")
        linked = 0
        for agent in agents:
            existing_user = db.query(User).filter(User.agent_id == agent.id).first()
            if existing_user:
                continue

            # Check if a user with this phone already exists
            phone_user = db.query(User).filter(User.phone == agent.agent_phone_number).first()
            if phone_user:
                phone_user.role = "agent"
                phone_user.agent_id = agent.id
                db.commit()
                print(f"  Linked existing user to {agent.agent_name}")
                linked += 1
                continue

            email = agent.email or f"agent_{agent.agent_phone_number.replace('+', '')}@weespas.com"
            if db.query(User).filter(User.email == email).first():
                email = f"agent_{str(uuid.uuid4())[:8]}@weespas.com"

            new_user = User(
                id=str(uuid.uuid4()),
                name=agent.agent_name,
                email=email,
                phone=agent.agent_phone_number,
                hashed_password=hash_password("agent123"),
                role="agent",
                agent_id=agent.id,
                is_active=True,
            )
            db.add(new_user)
            db.commit()
            print(f"  Created user for {agent.agent_name}: {email} / agent123")
            linked += 1

        # ── 7. Ensure admin user exists ──
        admin = db.query(User).filter(User.email == "admin@weespas.com").first()
        if not admin:
            admin = User(
                id=str(uuid.uuid4()),
                name="Weespas Admin",
                email="admin@weespas.com",
                phone="+254700000000",
                hashed_password=hash_password("admin123"),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print("  Created admin user: admin@weespas.com / admin123")

        # ── 8. Print summary ──
        print("\n" + "=" * 55)
        print("  STATS SEED COMPLETE — Dashboard Test Data Ready")
        print("=" * 55)

        total_props = db.query(Property).count()
        active_props = db.query(Property).filter(Property.is_active == True).count()
        total_views = db.query(func.sum(Property.view_count)).scalar() or 0
        for_sale = db.query(Property).filter(
            Property.is_active == True,
            Property.listing_type == PropertyListingType.SALE
        ).count()
        for_rent = db.query(Property).filter(
            Property.is_active == True,
            Property.listing_type == PropertyListingType.RENT
        ).count()
        featured = db.query(Property).filter(
            Property.is_active == True,
            Property.is_featured == True
        ).count()
        certified = db.query(Property).filter(
            Property.is_active == True,
            Property.is_engineer_certified == True
        ).count()

        print(f"\n  Properties:  {total_props} total ({active_props} active)")
        print(f"  For Sale:    {for_sale}")
        print(f"  For Rent:    {for_rent}")
        print(f"  Featured:    {featured}")
        print(f"  Certified:   {certified}")
        print(f"  Total Views: {total_views:,}")

        print(f"\n  Agents: {db.query(Agent).count()}")
        print(f"  Users:  {db.query(User).count()}")
        print(f"    - admin:  {db.query(User).filter(User.role == 'admin').count()}")
        print(f"    - agent:  {db.query(User).filter(User.role == 'agent').count()}")
        print(f"    - user:   {db.query(User).filter(User.role == 'user').count()}")

        print("\n  Per-agent breakdown:")
        for agent in agents:
            a_total = db.query(Property).filter(Property.agent_id == agent.id).count()
            a_views = db.query(func.sum(Property.view_count)).filter(
                Property.agent_id == agent.id
            ).scalar() or 0
            a_sale = db.query(Property).filter(
                Property.agent_id == agent.id,
                Property.listing_type == PropertyListingType.SALE,
                Property.is_active == True,
            ).count()
            a_rent = db.query(Property).filter(
                Property.agent_id == agent.id,
                Property.listing_type == PropertyListingType.RENT,
                Property.is_active == True,
            ).count()
            user = db.query(User).filter(User.agent_id == agent.id).first()
            email = user.email if user else "NO USER"
            print(f"    {agent.agent_name:20s}  props={a_total:3d}  views={a_views:5,d}  sale={a_sale}  rent={a_rent}  login={email}")

        print("\n  Test credentials:")
        print("    Admin:  admin@weespas.com / admin123")
        print("    Agents: <agent_email> / agent123")
        print()

    except Exception as e:
        print(f"\nERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_stats()
