#!/usr/bin/env python3
"""
Expanded seed: 7 new agents + 100 diverse properties across Kenya.
Run after the initial seed.py — this adds to existing data.
"""

import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import (
    Property, Agent, Address, PropertyImage, PropertyVideo,
    PropertyCategory, PropertyListingType
)
import uuid
from decimal import Decimal
import random

random.seed(42)

# Unsplash image pools by category
HOUSE_IMAGES = [
    "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=600&h=400",
    "https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=600&h=400",
    "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=600&h=400",
    "https://images.unsplash.com/photo-1605276374104-dee2a0ed3cd6?w=600&h=400",
    "https://images.unsplash.com/photo-1583608205776-bfd35f0d9f83?w=600&h=400",
    "https://images.unsplash.com/photo-1570129477492-45c003edd2be?w=600&h=400",
]
APT_IMAGES = [
    "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=600&h=400",
    "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=600&h=400",
    "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=600&h=400",
    "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=600&h=400",
]
VILLA_IMAGES = [
    "https://images.unsplash.com/photo-1613490493576-7fde63acd811?w=600&h=400",
    "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?w=600&h=400",
    "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?w=600&h=400",
]
OFFICE_IMAGES = [
    "https://images.unsplash.com/photo-1497366216548-37526070297c?w=600&h=400",
    "https://images.unsplash.com/photo-1497366811353-6870744d04b2?w=600&h=400",
    "https://images.unsplash.com/photo-1524758631624-e2822e304c36?w=600&h=400",
]
LAND_IMAGES = [
    "https://images.unsplash.com/photo-1500382017468-9049fed747ef?w=600&h=400",
    "https://images.unsplash.com/photo-1628624747186-a941c476b7ef?w=600&h=400",
]
COMMERCIAL_IMAGES = [
    "https://images.unsplash.com/photo-1441986300917-64674bd600d8?w=600&h=400",
    "https://images.unsplash.com/photo-1604719312566-8912e9227c6a?w=600&h=400",
    "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=600&h=400",
]

IMAGE_MAP = {
    "house": HOUSE_IMAGES,
    "apartment": APT_IMAGES,
    "villa": VILLA_IMAGES,
    "studio": APT_IMAGES,
    "office": OFFICE_IMAGES,
    "land": LAND_IMAGES,
    "warehouse": COMMERCIAL_IMAGES,
    "shop": COMMERCIAL_IMAGES,
    "kiosk": COMMERCIAL_IMAGES,
}


def seed_expanded():
    db = SessionLocal()

    try:
        # Load category map
        categories = {}
        for cat in db.query(PropertyCategory).all():
            categories[cat.slug] = cat

        # ===================== 7 NEW AGENTS =====================
        print("Creating 7 new agents...")
        new_agents_data = [
            ("Grace Wanjiku", "+254711223344", "grace@weespas.com",
             "Top-rated residential agent in Nairobi with 8 years experience. Specializes in family homes in Kileleshwa and Lavington."),
            ("Peter Kamau", "+254722556677", "peter@weespas.com",
             "Commercial property specialist. Handles office spaces, warehouses and retail in Westlands and Upper Hill."),
            ("Amina Hassan", "+254733889900", "amina@weespas.com",
             "Mombasa's leading coastal property expert. Luxury beachfront homes and holiday rentals."),
            ("James Otieno", "+254744112233", "james@weespas.com",
             "Kisumu and Western Kenya expert. Land, farms and lakeside properties."),
            ("Faith Njeri", "+254755334455", "faith@weespas.com",
             "Affordable housing specialist. Studios, bedsitters and 1-bedroom apartments for young professionals."),
            ("Brian Mutua", "+254766556677", "brian@weespas.com",
             "Luxury segment. Villas in Karen, Runda, and Muthaiga. High-net-worth client portfolio."),
            ("Diana Chebet", "+254777889900", "diana@weespas.com",
             "Nakuru and Rift Valley properties. Residential estates, agricultural land and commercial plots."),
        ]

        agents = []
        for name, phone, email, bio in new_agents_data:
            agent = Agent(
                id=str(uuid.uuid4()),
                agent_name=name,
                agent_phone_number=phone,
                email=email,
                bio=bio,
                is_verified=random.choice([True, True, True, False]),
                is_active=True,
            )
            db.add(agent)
            agents.append(agent)
            print(f"  + {name}")

        db.commit()

        # Also include existing agents in the pool
        existing_agents = db.query(Agent).all()
        all_agents = existing_agents

        # ===================== 100 PROPERTIES =====================
        print("\nCreating 100 properties...")

        # Kenyan locations with realistic coordinates
        locations = [
            # Nairobi
            ("Westlands, Nairobi", -1.2662, 36.8142, "Nairobi", "Nairobi County"),
            ("Kilimani, Nairobi", -1.2921, 36.7758, "Nairobi", "Nairobi County"),
            ("Karen, Nairobi", -1.3634, 36.6877, "Nairobi", "Nairobi County"),
            ("Lavington, Nairobi", -1.2780, 36.7720, "Nairobi", "Nairobi County"),
            ("Runda, Nairobi", -1.2166, 36.8090, "Nairobi", "Nairobi County"),
            ("Kileleshwa, Nairobi", -1.2750, 36.7850, "Nairobi", "Nairobi County"),
            ("South B, Nairobi", -1.3100, 36.8350, "Nairobi", "Nairobi County"),
            ("South C, Nairobi", -1.3150, 36.8250, "Nairobi", "Nairobi County"),
            ("Donholm, Nairobi", -1.2950, 36.8800, "Nairobi", "Nairobi County"),
            ("Langata, Nairobi", -1.3500, 36.7500, "Nairobi", "Nairobi County"),
            ("Upperhill, Nairobi", -1.2897, 36.8202, "Nairobi", "Nairobi County"),
            ("Parklands, Nairobi", -1.2580, 36.8120, "Nairobi", "Nairobi County"),
            ("Embakasi, Nairobi", -1.3230, 36.9100, "Nairobi", "Nairobi County"),
            ("Ruaka, Nairobi", -1.2100, 36.7700, "Nairobi", "Nairobi County"),
            ("Syokimau, Nairobi", -1.3750, 36.9250, "Nairobi", "Nairobi County"),
            ("Athi River", -1.4580, 36.9820, "Athi River", "Machakos County"),
            ("Kitengela", -1.4700, 36.9600, "Kitengela", "Kajiado County"),
            ("Ngong, Kajiado", -1.3630, 36.6550, "Ngong", "Kajiado County"),
            ("Thika Town", -1.0396, 37.0900, "Thika", "Kiambu County"),
            ("Juja, Kiambu", -1.1050, 37.0130, "Juja", "Kiambu County"),
            # Mombasa
            ("Nyali, Mombasa", -4.0200, 39.7100, "Mombasa", "Mombasa County"),
            ("Bamburi, Mombasa", -3.9900, 39.7200, "Mombasa", "Mombasa County"),
            ("Diani Beach, Kwale", -4.3170, 39.5830, "Diani", "Kwale County"),
            ("Shanzu, Mombasa", -3.9700, 39.7300, "Mombasa", "Mombasa County"),
            ("Mtwapa, Kilifi", -3.9400, 39.7350, "Mtwapa", "Kilifi County"),
            # Kisumu
            ("Milimani, Kisumu", -0.0917, 34.7680, "Kisumu", "Kisumu County"),
            ("Mamboleo, Kisumu", -0.0750, 34.7900, "Kisumu", "Kisumu County"),
            # Nakuru
            ("Milimani, Nakuru", -0.2833, 36.0700, "Nakuru", "Nakuru County"),
            ("Naka Estate, Nakuru", -0.3000, 36.0800, "Nakuru", "Nakuru County"),
            # Eldoret
            ("Elgon View, Eldoret", 0.5200, 35.2700, "Eldoret", "Uasin Gishu County"),
            # Nanyuki
            ("Nanyuki Town", 0.0060, 37.0720, "Nanyuki", "Laikipia County"),
            # Naivasha
            ("Naivasha Town", -0.7172, 36.4310, "Naivasha", "Nakuru County"),
        ]

        properties_data = [
            # ========== HOUSES FOR SALE (25) ==========
            ("Elegant 4-Bed House in Karen", "Spacious family home on a half-acre plot with mature gardens, staff quarters, and a double garage. Recently renovated kitchen and bathrooms.", "sale", "house", 4, 3, "450 sqm", 450.0, Decimal("35000000"), True, True, 2),
            ("3-Bedroom Bungalow in Langata", "Well-maintained single-story bungalow in a quiet cul-de-sac. Large backyard ideal for children. Near Langata Road.", "sale", "house", 3, 2, "250 sqm", 250.0, Decimal("18500000"), True, False, 1),
            ("Modern 5-Bed Townhouse in Lavington", "Contemporary design with rooftop terrace, home office, and smart home features. Gated community with 24hr security.", "sale", "house", 5, 4, "380 sqm", 380.0, Decimal("42000000"), True, True, 2),
            ("2-Bedroom Starter Home in Syokimau", "Perfect first home for a young couple. Open plan living, modern finishes. Walking distance to SGR station.", "sale", "house", 2, 1, "120 sqm", 120.0, Decimal("6800000"), False, False, 1),
            ("4-Bed Maisonette in South C", "Double-story family home with DSQ. All bedrooms en-suite. Parking for 3 cars. Close to schools.", "sale", "house", 4, 4, "280 sqm", 280.0, Decimal("22000000"), True, False, 2),
            ("6-Bed Mansion in Runda", "Executive home with infinity pool, home cinema, wine cellar, and panoramic garden views. Triple garage.", "sale", "house", 6, 5, "650 sqm", 650.0, Decimal("85000000"), True, True, 2),
            ("3-Bed House in Kitengela", "Affordable gated estate home with garden. Near Kitengela town centre. Community amenities.", "sale", "house", 3, 2, "180 sqm", 180.0, Decimal("7500000"), False, False, 1),
            ("4-Bed House in Thika Town", "Modern family home in a developing estate. Large compound, ample parking. Near Thika superhighway.", "sale", "house", 4, 3, "300 sqm", 300.0, Decimal("12000000"), True, False, 2),
            ("5-Bed Colonial House in Muthaiga", "Classic colonial architecture on 1 acre with tennis court, guest house, and mature indigenous trees.", "sale", "house", 5, 4, "520 sqm", 520.0, Decimal("120000000"), True, True, 3),
            ("3-Bed House in Athi River", "Gated community home with modern finishes. Close to EPZ and major factories. Good rental potential.", "sale", "house", 3, 2, "200 sqm", 200.0, Decimal("8200000"), False, False, 1),
            ("3-Bed Bungalow in Ngong", "Hillside home with spectacular Rift Valley views. Cool climate, well-maintained garden.", "sale", "house", 3, 2, "220 sqm", 220.0, Decimal("14500000"), True, False, 2),
            ("4-Bed House in Juja", "Near JKUAT university. Good for owner-occupation or student rental investment.", "sale", "house", 4, 3, "260 sqm", 260.0, Decimal("9800000"), False, False, 1),
            ("Modern 3-Bed in Ruaka", "Open-plan design, high ceilings, large windows. Walking distance to Two Rivers Mall.", "sale", "house", 3, 2, "190 sqm", 190.0, Decimal("15800000"), True, True, 2),
            ("5-Bed Home in Nyali Mombasa", "Coastal home with ocean breeze, large verandah. Near Nyali Beach. Perfect for family.", "sale", "house", 5, 4, "400 sqm", 400.0, Decimal("28000000"), True, False, 2),
            ("4-Bed House in Milimani Nakuru", "Modern estate home with mountain views. Quiet neighbourhood near Nakuru CBD.", "sale", "house", 4, 3, "280 sqm", 280.0, Decimal("16000000"), True, False, 2),

            # ========== HOUSES FOR RENT (10) ==========
            ("3-Bed House for Rent in Kileleshwa", "Fully furnished executive home with garden and DSQ. Available immediately.", "rent", "house", 3, 2, "240 sqm", 240.0, Decimal("120000"), True, False, 2),
            ("4-Bed House for Rent in Karen", "Unfurnished family home on quiet lane. Large garden, servant quarters included.", "rent", "house", 4, 3, "350 sqm", 350.0, Decimal("180000"), True, True, 2),
            ("2-Bed House for Rent in South B", "Compact home ideal for small family. Near bus routes and shopping centres.", "rent", "house", 2, 1, "130 sqm", 130.0, Decimal("45000"), False, False, 1),
            ("3-Bed Townhouse for Rent in Lavington", "Gated compound with shared pool and gym. All bedrooms en-suite.", "rent", "house", 3, 3, "220 sqm", 220.0, Decimal("150000"), True, False, 2),
            ("5-Bed House for Rent in Runda", "Executive rental with pool, garden, and home office. Ideal for diplomatic staff.", "rent", "house", 5, 4, "500 sqm", 500.0, Decimal("350000"), True, True, 3),
            ("3-Bed House for Rent in Diani", "Beachside home with tropical garden. Perfect holiday or permanent residence.", "rent", "house", 3, 2, "200 sqm", 200.0, Decimal("80000"), False, False, 1),
            ("2-Bed Cottage for Rent in Nanyuki", "Charming cottage near Mt. Kenya with fireplace and mountain views.", "rent", "house", 2, 1, "110 sqm", 110.0, Decimal("40000"), False, False, 1),
            ("4-Bed House for Rent in Eldoret", "Modern home in Elgon View estate. Close to international schools.", "rent", "house", 4, 3, "300 sqm", 300.0, Decimal("65000"), True, False, 2),
            ("3-Bed House for Rent in Kisumu", "Lakeside area home with spacious compound. Near Kisumu CBD.", "rent", "house", 3, 2, "200 sqm", 200.0, Decimal("55000"), False, False, 1),
            ("3-Bed House for Rent in Naivasha", "Country home near Lake Naivasha. Great weekend getaway or permanent residence.", "rent", "house", 3, 2, "180 sqm", 180.0, Decimal("50000"), False, False, 1),

            # ========== APARTMENTS FOR SALE (12) ==========
            ("2-Bed Apartment in Kilimani", "High-rise apartment with city skyline views. Swimming pool and gym in building.", "sale", "apartment", 2, 2, "95 sqm", 95.0, Decimal("8500000"), True, True, 2),
            ("3-Bed Penthouse in Westlands", "Top-floor penthouse with private rooftop terrace. 360-degree views of Nairobi.", "sale", "apartment", 3, 3, "180 sqm", 180.0, Decimal("25000000"), True, True, 3),
            ("1-Bed Apartment in Parklands", "Cozy apartment near Aga Khan Hospital. Ideal investment property with good rental yield.", "sale", "apartment", 1, 1, "55 sqm", 55.0, Decimal("4200000"), False, False, 1),
            ("2-Bed Apartment in Embakasi", "Affordable apartment in upcoming area. Near JKIA airport.", "sale", "apartment", 2, 1, "80 sqm", 80.0, Decimal("3800000"), False, False, 1),
            ("3-Bed Apartment in Upperhill", "Premium apartment complex with concierge, pool, and underground parking.", "sale", "apartment", 3, 2, "140 sqm", 140.0, Decimal("18000000"), True, True, 2),
            ("1-Bed Apartment in Donholm", "Entry-level investment. High demand rental area near Savannah Road.", "sale", "apartment", 1, 1, "45 sqm", 45.0, Decimal("2900000"), False, False, 1),
            ("2-Bed Apartment in Bamburi Mombasa", "Coastal apartment with sea breeze. Near Bamburi Beach.", "sale", "apartment", 2, 2, "90 sqm", 90.0, Decimal("6500000"), False, False, 2),
            ("3-Bed Apartment in Nyali", "Luxury apartment with ocean views. High-end finishes throughout.", "sale", "apartment", 3, 2, "150 sqm", 150.0, Decimal("15000000"), True, True, 2),

            # ========== APARTMENTS FOR RENT (8) ==========
            ("1-Bed Apartment for Rent in Westlands", "Furnished studio-style one bedroom. Walking distance to Sarit Centre.", "rent", "apartment", 1, 1, "50 sqm", 50.0, Decimal("35000"), False, False, 1),
            ("2-Bed Apartment for Rent in Kilimani", "Modern apartment with balcony and city views. Gym and pool access.", "rent", "apartment", 2, 2, "90 sqm", 90.0, Decimal("75000"), True, False, 2),
            ("3-Bed Apartment for Rent in Lavington", "Spacious family apartment in quiet compound. Dedicated parking.", "rent", "apartment", 3, 2, "130 sqm", 130.0, Decimal("95000"), True, False, 2),
            ("2-Bed Apartment for Rent in South C", "Well-located apartment near shopping and schools. Water and backup generator.", "rent", "apartment", 2, 1, "85 sqm", 85.0, Decimal("40000"), False, False, 1),

            # ========== VILLAS (8) ==========
            ("Luxury Villa in Karen with Pool", "Architect-designed 5-bed villa with heated infinity pool, landscaped gardens, and entertainment pavilion.", "sale", "villa", 5, 5, "600 sqm", 600.0, Decimal("95000000"), True, True, 3),
            ("Modern Villa in Runda Estate", "Contemporary 4-bed villa with home automation, solar panels, and rainwater harvesting.", "sale", "villa", 4, 4, "450 sqm", 450.0, Decimal("72000000"), True, True, 3),
            ("Beachfront Villa in Diani", "Stunning 4-bed oceanfront villa with private beach access and infinity pool overlooking the Indian Ocean.", "sale", "villa", 4, 3, "380 sqm", 380.0, Decimal("55000000"), True, True, 2),
            ("Villa for Rent in Karen", "Furnished executive villa available for diplomatic or corporate lease. Pool and tennis court.", "rent", "villa", 5, 4, "500 sqm", 500.0, Decimal("450000"), True, True, 3),
            ("Hilltop Villa in Ngong", "Panoramic Rift Valley views from every room. 3-bed villa with wrap-around verandah.", "sale", "villa", 3, 3, "320 sqm", 320.0, Decimal("38000000"), True, False, 2),
            ("Lakeside Villa in Naivasha", "Peaceful 4-bed retreat near Lake Naivasha. Ideal for eco-tourism or private residence.", "sale", "villa", 4, 3, "350 sqm", 350.0, Decimal("32000000"), True, False, 2),

            # ========== STUDIOS (6) ==========
            ("Studio Apartment in Westlands", "Modern studio with kitchenette and built-in wardrobes. High-speed internet ready.", "rent", "studio", 0, 1, "30 sqm", 30.0, Decimal("22000"), False, False, 1),
            ("Furnished Studio in Kilimani", "Fully furnished studio perfect for young professionals. All utilities included.", "rent", "studio", 0, 1, "35 sqm", 35.0, Decimal("28000"), False, False, 1),
            ("Studio for Sale in Ruaka", "Investment studio in high-demand area. Currently tenanted at KES 18,000/month.", "sale", "studio", 0, 1, "28 sqm", 28.0, Decimal("2200000"), False, False, 1),
            ("Studio Apartment in Mtwapa", "Coastal studio near entertainment strip. Popular with tourists and expats.", "rent", "studio", 0, 1, "32 sqm", 32.0, Decimal("15000"), False, False, 1),
            ("Premium Studio in Upperhill", "Executive studio in new high-rise with city views. Concierge and gym.", "rent", "studio", 0, 1, "40 sqm", 40.0, Decimal("35000"), True, False, 2),
            ("Studio for Sale in Parklands", "Compact investment unit near hospitals and universities. Strong rental demand.", "sale", "studio", 0, 1, "33 sqm", 33.0, Decimal("3100000"), False, False, 1),

            # ========== OFFICES (8) ==========
            ("Corner Office Space in Westlands", "200 sqm open-plan office in prime Westlands tower. Fiber internet, backup power.", "rent", "office", 0, 2, "200 sqm", 200.0, Decimal("180000"), True, True, 2),
            ("Co-working Space in Kilimani", "Fully fitted shared office space. Meeting rooms, reception, kitchen included.", "rent", "office", 0, 1, "80 sqm", 80.0, Decimal("55000"), False, False, 1),
            ("Office Building for Sale in Upperhill", "5-floor commercial building. Fully tenanted. 8% yield. Prime investment.", "sale", "office", 0, 10, "2000 sqm", 2000.0, Decimal("350000000"), True, True, 3),
            ("Small Office in Parklands", "Ground floor office suitable for clinic, law firm, or consultancy.", "rent", "office", 0, 1, "60 sqm", 60.0, Decimal("40000"), False, False, 1),
            ("Modern Office Suite in Mombasa", "Sea-view office in Nyali business district. Air-conditioned with parking.", "rent", "office", 0, 2, "120 sqm", 120.0, Decimal("85000"), True, False, 2),
            ("Tech Hub Office in Westlands", "Startup-ready space with server room, CAT6 cabling, and 100Mbps dedicated line.", "rent", "office", 0, 2, "150 sqm", 150.0, Decimal("120000"), True, False, 2),

            # ========== LAND (10) ==========
            ("Quarter Acre in Karen", "Residential plot in prime Karen. Ready title deed. All services connected.", "sale", "land", 0, 0, "1012 sqm", 1012.0, Decimal("25000000"), False, True, 2),
            ("Half Acre in Runda", "Exclusive neighbourhood plot. Mature trees, gentle slope. Perfect for custom build.", "sale", "land", 0, 0, "2023 sqm", 2023.0, Decimal("50000000"), False, False, 3),
            ("50x100 Plot in Kitengela", "Affordable plot in developing area. Near tarmac road. Clean title.", "sale", "land", 0, 0, "465 sqm", 465.0, Decimal("1800000"), False, False, 1),
            ("1 Acre in Nanyuki", "Agricultural/residential land with Mt. Kenya views. Borehole water available.", "sale", "land", 0, 0, "4047 sqm", 4047.0, Decimal("5500000"), False, False, 1),
            ("Commercial Plot in Thika", "Prime commercial plot on Thika-Garissa highway frontage. Ideal for fuel station or mall.", "sale", "land", 0, 0, "2000 sqm", 2000.0, Decimal("45000000"), False, True, 2),
            ("Beach Plot in Diani", "Oceanfront plot in exclusive Diani area. Perfect for boutique hotel or private villa.", "sale", "land", 0, 0, "1500 sqm", 1500.0, Decimal("35000000"), False, True, 2),
            ("5 Acres in Naivasha", "Fertile agricultural land near Lake Naivasha. Suitable for horticulture.", "sale", "land", 0, 0, "20234 sqm", 20234.0, Decimal("12000000"), False, False, 1),
            ("Plot in Milimani Kisumu", "Residential plot in upmarket Kisumu area. Ready for development.", "sale", "land", 0, 0, "800 sqm", 800.0, Decimal("6000000"), False, False, 1),
            ("2 Acres in Eldoret", "Land along Eldoret-Iten road. Commercial or residential zoning.", "sale", "land", 0, 0, "8094 sqm", 8094.0, Decimal("15000000"), False, False, 2),
            ("Eighth Acre in Athi River", "Affordable plot in gated community. Water and electricity at boundary.", "sale", "land", 0, 0, "506 sqm", 506.0, Decimal("1200000"), False, False, 1),

            # ========== WAREHOUSES (5) ==========
            ("5000 sqft Warehouse in Athi River", "Industrial warehouse near Mombasa Road. Loading bay, 3-phase power, 24hr security.", "rent", "warehouse", 0, 2, "465 sqm", 465.0, Decimal("200000"), True, False, 2),
            ("Modern Warehouse in Ruiru", "New build warehouse with office block. Easy access to Thika superhighway.", "sale", "warehouse", 0, 2, "1000 sqm", 1000.0, Decimal("80000000"), True, True, 2),
            ("Cold Storage Warehouse in Embakasi", "Temperature-controlled warehouse near JKIA. Ideal for logistics and fresh produce.", "rent", "warehouse", 0, 1, "800 sqm", 800.0, Decimal("350000"), True, False, 2),
            ("Mini Warehouse in Mombasa", "Port-adjacent storage facility. Container parking available.", "rent", "warehouse", 0, 1, "300 sqm", 300.0, Decimal("120000"), False, False, 1),
            ("Godown in Industrial Area Nairobi", "Large godown on Lunga Lunga Road. High roof, heavy vehicle access.", "rent", "warehouse", 0, 2, "1500 sqm", 1500.0, Decimal("500000"), True, False, 2),

            # ========== SHOPS (5) ==========
            ("Retail Shop in Westgate Mall Area", "High-traffic retail space near Westgate. Glass frontage, air-conditioned.", "rent", "shop", 0, 1, "60 sqm", 60.0, Decimal("150000"), True, True, 2),
            ("Shop for Sale in Mombasa CBD", "Ground floor shop on busy Digo Road. Strong footfall. Sitting tenant.", "sale", "shop", 0, 1, "45 sqm", 45.0, Decimal("8000000"), False, False, 1),
            ("Corner Shop in Nakuru Town", "Prime location at busy intersection. Suitable for pharmacy, boutique, or electronics.", "rent", "shop", 0, 1, "40 sqm", 40.0, Decimal("35000"), False, False, 1),
            ("Shop Space in Two Rivers Mall", "Premium retail unit in East Africa's largest mall. Category A location.", "rent", "shop", 0, 1, "80 sqm", 80.0, Decimal("250000"), True, True, 2),
            ("Retail Space in Kisumu CBD", "Shop on main street with good display window. Near municipal market.", "rent", "shop", 0, 1, "35 sqm", 35.0, Decimal("25000"), False, False, 1),

            # ========== KIOSKS (3) ==========
            ("Food Kiosk Space in Westlands", "Street-facing kiosk in busy business district. Water and electricity connected.", "rent", "kiosk", 0, 0, "8 sqm", 8.0, Decimal("15000"), False, False, 1),
            ("Kiosk in Kitengela Market", "Market kiosk in high-traffic area. Ideal for M-Pesa or small retail.", "rent", "kiosk", 0, 0, "6 sqm", 6.0, Decimal("8000"), False, False, 1),
            ("Container Kiosk in Embakasi", "Converted shipping container with electricity. Near residential estate.", "rent", "kiosk", 0, 0, "12 sqm", 12.0, Decimal("12000"), False, False, 1),

            # ========== ADDITIONAL MIXED PROPERTIES (22 more to reach 100) ==========
            ("Penthouse Duplex in Kileleshwa", "Rare 3-bed duplex penthouse with private roof garden and panoramic views. Chef's kitchen.", "sale", "apartment", 3, 3, "200 sqm", 200.0, Decimal("32000000"), True, True, 3),
            ("4-Bed House in Parklands", "Colonial-era charm with modern upgrades. Hardwood floors, high ceilings, large compound.", "sale", "house", 4, 3, "350 sqm", 350.0, Decimal("28000000"), True, False, 2),
            ("Bedsitter for Rent in Donholm", "Clean bedsitter with separate kitchen. Water 24/7. Near Donholm Phase 5.", "rent", "studio", 0, 1, "20 sqm", 20.0, Decimal("8000"), False, False, 1),
            ("Serviced Office in Kilimani", "Plug-and-play office with reception, boardroom, and admin support included.", "rent", "office", 0, 1, "100 sqm", 100.0, Decimal("95000"), True, False, 2),
            ("10 Acres Farm in Nanyuki", "Fertile farmland with river frontage. Ideal for horticulture or mixed farming.", "sale", "land", 0, 0, "40470 sqm", 40470.0, Decimal("25000000"), False, False, 1),
            ("2-Bed Apartment for Rent in Shanzu", "Beachside apartment with pool. Fully furnished for short or long term stay.", "rent", "apartment", 2, 1, "75 sqm", 75.0, Decimal("45000"), False, False, 1),
            ("Luxury 3-Bed Villa for Rent in Nyali", "Gated villa with private garden, pool, and ocean views. Staff quarters.", "rent", "villa", 3, 3, "350 sqm", 350.0, Decimal("200000"), True, True, 3),
            ("Warehouse for Sale in Athi River", "Large industrial warehouse on 1 acre. 3-phase power, borehole, paved yard.", "sale", "warehouse", 0, 2, "2000 sqm", 2000.0, Decimal("120000000"), True, False, 2),
            ("Shop in Naka Estate Nakuru", "Busy shopping centre unit. Ideal for salon, pharmacy, or retail.", "rent", "shop", 0, 1, "30 sqm", 30.0, Decimal("18000"), False, False, 1),
            ("3-Bed House for Rent in Ruaka", "Modern townhouse in gated community. Near shopping centres and schools.", "rent", "house", 3, 2, "160 sqm", 160.0, Decimal("65000"), True, False, 2),
            ("Executive 4-Bed in Kileleshwa", "High-end home with home cinema, wine cellar, and manicured gardens.", "sale", "house", 4, 4, "400 sqm", 400.0, Decimal("55000000"), True, True, 3),
            ("1-Bed Apartment in Mombasa CBD", "City centre apartment near ferry. Ideal for young professional or Airbnb.", "sale", "apartment", 1, 1, "40 sqm", 40.0, Decimal("2800000"), False, False, 1),
            ("Commercial Plot in Westlands", "Prime 0.5 acre commercial plot on Waiyaki Way. Approved for mixed-use development.", "sale", "land", 0, 0, "2023 sqm", 2023.0, Decimal("200000000"), False, True, 1),
            ("3-Bed Apartment for Rent in Upperhill", "Corporate housing apartment with city views. Gym, pool, and concierge.", "rent", "apartment", 3, 2, "120 sqm", 120.0, Decimal("110000"), True, False, 2),
            ("Godown for Rent in Mombasa Port", "Strategic storage facility near Kilindini harbour. Heavy duty floor, high clearance.", "rent", "warehouse", 0, 1, "600 sqm", 600.0, Decimal("280000"), True, False, 1),
            ("4-Bed House in Naka Nakuru", "New build in upcoming estate. Solar heated water, borehole. Mountain views.", "sale", "house", 4, 3, "240 sqm", 240.0, Decimal("11500000"), True, False, 2),
            ("2-Bed Apartment for Sale in Kisumu", "Lake-view apartment in Milimani. Modern finishes, ample parking.", "sale", "apartment", 2, 2, "100 sqm", 100.0, Decimal("5800000"), False, False, 1),
            ("Furnished Studio for Rent in Nairobi CBD", "Central location studio. Walk to offices, restaurants, and entertainment.", "rent", "studio", 0, 1, "25 sqm", 25.0, Decimal("20000"), False, False, 1),
            ("Retail Space in Mama Ngina Street", "Premium ground floor retail on Nairobi's busiest pedestrian street.", "rent", "shop", 0, 1, "55 sqm", 55.0, Decimal("180000"), True, True, 2),
            ("Quarter Acre in Juja", "Residential plot near JKUAT. Clean title, ready for development.", "sale", "land", 0, 0, "1012 sqm", 1012.0, Decimal("3500000"), False, False, 1),
            ("3-Bed Townhouse for Sale in Syokimau", "Modern end-unit townhouse with rooftop. Near SGR station. Family-friendly estate.", "sale", "house", 3, 2, "170 sqm", 170.0, Decimal("9500000"), True, False, 2),
            ("Office Space for Rent in Eldoret", "Modern office in Eldoret CBD. Suitable for NGO, consultancy, or bank branch.", "rent", "office", 0, 2, "180 sqm", 180.0, Decimal("70000"), False, False, 1),
        ]

        count = 0
        for (title, desc, lt, cat_slug, beds, baths, size, size_num, price, certified, featured, img_count) in properties_data:
            listing_type = PropertyListingType.SALE if lt == "sale" else PropertyListingType.RENT
            agent = random.choice(all_agents)
            loc = random.choice(locations)

            # Slight coordinate jitter so properties aren't stacked exactly
            lat = loc[1] + random.uniform(-0.008, 0.008)
            lng = loc[2] + random.uniform(-0.008, 0.008)

            prop = Property(
                id=str(uuid.uuid4()),
                title=title,
                description=desc,
                price=price,
                currency="KES",
                listing_type=listing_type,
                category_id=categories[cat_slug].id,
                is_engineer_certified=certified,
                is_featured=featured,
                is_active=True,
                agent_id=agent.id,
                bedrooms=beds if beds > 0 else None,
                bathrooms=baths if baths > 0 else None,
                size=size,
                size_numeric=size_num,
                parking_spaces=random.choice([None, 1, 2, 3]) if beds > 0 else None,
                year_built=random.choice([None, 2018, 2019, 2020, 2021, 2022, 2023, 2024]) if cat_slug not in ("land", "kiosk") else None,
            )

            address = Address(
                id=str(uuid.uuid4()),
                location_name=loc[0],
                latitude=round(lat, 6),
                longitude=round(lng, 6),
                city=loc[3],
                county=loc[4],
                country="Kenya",
            )
            address.property = prop
            db.add(prop)
            db.add(address)

            # Add images
            img_pool = IMAGE_MAP.get(cat_slug, COMMERCIAL_IMAGES)
            for i in range(min(img_count, len(img_pool))):
                img_url = img_pool[i % len(img_pool)]
                image = PropertyImage(
                    id=str(uuid.uuid4()),
                    property_id=prop.id,
                    url=img_url,
                    thumbnail_url=img_url.replace("w=600", "w=200").replace("h=400", "h=150"),
                    alt_text=f"{title} - Image {i + 1}",
                    order=i,
                    is_main=(i == 0),
                )
                db.add(image)

            count += 1
            if count % 10 == 0:
                print(f"  ... {count}/100 created")

        db.commit()

        # Final counts
        total_agents = db.query(Agent).count()
        total_props = db.query(Property).count()
        total_images = db.query(PropertyImage).count()
        print(f"\nDone! Database now has:")
        print(f"  Agents:     {total_agents}")
        print(f"  Properties: {total_props}")
        print(f"  Images:     {total_images}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    seed_expanded()
