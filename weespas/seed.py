#!/usr/bin/env python3
"""
Seed script for populating Weespas database with test data.
Creates: Categories, Agents, Properties, Addresses, Images, Videos.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from PE.weespas.core.database import SessionLocal, create_tables
from PE.weespas.models.property import (
    Property, Agent, Address, PropertyImage, PropertyVideo,
    PropertyCategory, PropertyListingType, PropertyCategoryEnum
)
import uuid
from decimal import Decimal


def seed_database():
    """Seed the database with test data"""
    create_tables()
    db = SessionLocal()
    
    try:
        # Check if data already exists
        if db.query(Property).count() > 0:
            print("✓ Database already seeded. Skipping.")
            return
        
        print("🌱 Starting database seeding...\n")
        
        # ===================== SEED CATEGORIES =====================
        print("📁 Creating property categories...")
        categories_data = [
            ("House", "house", "Single family home"),
            ("Apartment", "apartment", "Multi-unit residential building"),
            ("Villa", "villa", "Luxury standalone residence"),
            ("Studio", "studio", "Compact single-room apartment"),
            ("Office", "office", "Commercial office space"),
            ("Land", "land", "Undeveloped property"),
            ("Warehouse", "warehouse", "Storage or industrial space"),
            ("Shop", "shop", "Retail shop space"),
            ("Kiosk", "kiosk", "Small retail kiosk"),
            ("Container", "container", "Converted shipping container unit"),
            ("Stall", "stall", "Market or roadside stall"),
            ("Commercial Space", "commercial_space", "General commercial premises"),
            ("Other", "other", "Uncategorized property type"),
        ]
        
        categories = {}
        for name, slug, description in categories_data:
            cat = PropertyCategory(
                id=str(uuid.uuid4()),
                name=name,
                slug=slug,
                description=description,
                display_order=list(categories_data).index((name, slug, description))
            )
            db.add(cat)
            categories[slug] = cat
            print(f"  ✓ {name}")
        
        db.commit()
        
        # ===================== SEED AGENTS =====================
        print("\n👤 Creating agents...")
        agents_data = [
            ("John Kipchoge", "+254713083378", "john@weespas.com", "Experienced agent with 10 years in real estate"),
            ("Sarah Muthoni", "+254720456789", "sarah@weespas.com", "Specialist in luxury properties"),
            ("David Okoye", "+254722334455", "david@weespas.com", "Commercial property expert"),
        ]
        
        agents = []
        for name, phone, email, bio in agents_data:
            agent = Agent(
                id=str(uuid.uuid4()),
                agent_name=name,
                agent_phone_number=phone,
                email=email,
                bio=bio,
                is_verified=True,
                is_active=True
            )
            db.add(agent)
            agents.append(agent)
            print(f"  ✓ {name} ({phone})")
        
        db.commit()
        
        # ===================== SEED PROPERTIES =====================
        print("\n🏠 Creating properties...")
        properties_data = [
            {
                "title": "Modern 3-Bedroom House in Westlands",
                "description": "Beautiful modern house with spacious garden, perfect for families.",
                "price": Decimal("8500000.00"),
                "listing_type": PropertyListingType.SALE,
                "category": "house",
                "bedrooms": 3,
                "bathrooms": 2,
                "size": "300 sqft",
                "size_numeric": 300.0,
                "location_name": "Westlands, Nairobi",
                "latitude": -1.2662,
                "longitude": 36.8142,
                "agent_idx": 0,
                "is_engineer_certified": True,
                "is_featured": True,
            },
            {
                "title": "Luxury 2-Bedroom Apartment in Kilimani",
                "description": "Premium apartment with modern amenities and city views.",
                "price": Decimal("5200000.00"),
                "listing_type": PropertyListingType.SALE,
                "category": "apartment",
                "bedrooms": 2,
                "bathrooms": 2,
                "size": "180 sqft",
                "size_numeric": 180.0,
                "location_name": "Kilimani, Nairobi",
                "latitude": -1.2921,
                "longitude": 36.7758,
                "agent_idx": 1,
                "is_engineer_certified": False,
                "is_featured": True,
            },
            {
                "title": "Contemporary Villa in Karen",
                "description": "Spacious villa with infinity pool, perfect for luxury living.",
                "price": Decimal("25000000.00"),
                "listing_type": PropertyListingType.SALE,
                "category": "villa",
                "bedrooms": 5,
                "bathrooms": 4,
                "size": "800 sqft",
                "size_numeric": 800.0,
                "location_name": "Karen, Nairobi",
                "latitude": -1.3634,
                "longitude": 36.6877,
                "agent_idx": 1,
                "is_engineer_certified": True,
                "is_featured": False,
            },
            {
                "title": "Studio Apartment for Rent in CBD",
                "description": "Compact, fully furnished studio in the heart of Nairobi CBD.",
                "price": Decimal("25000.00"),
                "listing_type": PropertyListingType.RENT,
                "category": "studio",
                "bedrooms": 0,
                "bathrooms": 1,
                "size": "50 sqft",
                "size_numeric": 50.0,
                "location_name": "Nairobi CBD",
                "latitude": -1.2833,
                "longitude": 36.8167,
                "agent_idx": 2,
                "is_engineer_certified": False,
                "is_featured": False,
            },
            {
                "title": "Prime Commercial Office Space in Westlands",
                "description": "Professional office space with parking, ideal for startups and corporations.",
                "price": Decimal("150000.00"),
                "listing_type": PropertyListingType.RENT,
                "category": "office",
                "bedrooms": 0,
                "bathrooms": 2,
                "size": "500 sqft",
                "size_numeric": 500.0,
                "location_name": "Westlands, Nairobi",
                "latitude": -1.2662,
                "longitude": 36.8142,
                "agent_idx": 0,
                "is_engineer_certified": True,
                "is_featured": False,
            },
            {
                "title": "Land Plot in Upper Hill",
                "description": "Prime residential land with excellent investment potential.",
                "price": Decimal("12000000.00"),
                "listing_type": PropertyListingType.SALE,
                "category": "land",
                "bedrooms": 0,
                "bathrooms": 0,
                "size": "2000 sqft",
                "size_numeric": 2000.0,
                "location_name": "Upper Hill, Nairobi",
                "latitude": -1.2897,
                "longitude": 36.8202,
                "agent_idx": 2,
                "is_engineer_certified": False,
                "is_featured": False,
            },
        ]
        
        properties = []
        for prop_data in properties_data:
            agent_idx = prop_data.pop("agent_idx")
            category_slug = prop_data.pop("category")
            lat = prop_data.pop("latitude")
            lon = prop_data.pop("longitude")
            location_name = prop_data.pop("location_name")
            
            # Create property — use the actual category UUID, not the slug string
            property_obj = Property(
                id=str(uuid.uuid4()),
                **prop_data,
                category_id=categories[category_slug].id,
                agent_id=agents[agent_idx].id,
            )
            
            # Create address
            address = Address(
                id=str(uuid.uuid4()),
                location_name=location_name,
                latitude=lat,
                longitude=lon,
                city="Nairobi",
                county="Nairobi County",
                country="Kenya"
            )
            address.property = property_obj
            
            db.add(property_obj)
            db.add(address)
            properties.append((property_obj, prop_data["title"]))
            print(f"  ✓ {prop_data['title']}")
        
        db.commit()
        
        # ===================== SEED IMAGES =====================
        print("\n🖼️  Adding property images...")
        image_urls = [
            "https://images.unsplash.com/photo-1568605114967-8130f3a36994?w=500&h=500",
            "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=500&h=500",
            "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=500&h=500",
            "https://images.unsplash.com/photo-1469022563149-aa64dbd37191?w=500&h=500",
            "https://images.unsplash.com/photo-1511632765486-a01980e01a18?w=500&h=500",
            "https://images.unsplash.com/photo-1487613810519-e21cc028cb29?w=500&h=500",
        ]
        
        for idx, (prop, title) in enumerate(properties):
            for img_idx, img_url in enumerate(image_urls[:2]):
                image = PropertyImage(
                    id=str(uuid.uuid4()),
                    property_id=prop.id,
                    url=img_url,
                    thumbnail_url=img_url.replace("w=500", "w=150"),
                    alt_text=f"{title} - Image {img_idx + 1}",
                    order=img_idx,
                    is_main=(img_idx == 0)
                )
                db.add(image)
            print(f"  ✓ Images for: {title}")
        
        db.commit()
        
        # ===================== SEED VIDEOS =====================
        print("\n🎬 Adding property videos...")
        video_intro = "https://commondatastorage.googleapis.com/gtv-videos-library/sample/BigBuckBunny.mp4"
        
        for idx, (prop, title) in enumerate(properties[:3]):
            video = PropertyVideo(
                id=str(uuid.uuid4()),
                property_id=prop.id,
                url=video_intro,
                thumbnail_url="https://images.unsplash.com/photo-1485846234645-a62644f84728?w=300&h=300",
                title=f"Virtual Tour - {title}",
                description="Complete walkthrough of the property",
                duration=120
            )
            db.add(video)
            print(f"  ✓ Video for: {title}")
        
        db.commit()
        
        print("\n✅ Database seeding completed successfully!\n")
        print(f"📊 Summary:")
        print(f"  • Categories: {len(categories)}")
        print(f"  • Agents: {len(agents)}")
        print(f"  • Properties: {len(properties)}")
        print(f"  • Images: {sum(2 for _ in properties)}")
        print(f"  • Videos: 3")
        
    except Exception as e:
        print(f"❌ Error seeding database: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()