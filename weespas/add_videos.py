#!/usr/bin/env python3
"""
One-time script: Add video tours to ALL properties in the database.
Run after initial seed. Safe to re-run — skips properties that already have videos.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.property import Property, PropertyVideo
import uuid


# Free sample videos (public domain / CC0) that actually play in browsers
SAMPLE_VIDEOS = [
    {
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        "streaming_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        "thumbnail_url": "https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=400&h=250&fit=crop",
        "duration": 15,
        "label": "Exterior Tour",
    },
    {
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
        "streaming_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
        "thumbnail_url": "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=400&h=250&fit=crop",
        "duration": 15,
        "label": "Interior Walkthrough",
    },
    {
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerFun.mp4",
        "streaming_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerFun.mp4",
        "thumbnail_url": "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?w=400&h=250&fit=crop",
        "duration": 60,
        "label": "Full Property Tour",
    },
    {
        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4",
        "streaming_url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4",
        "thumbnail_url": "https://images.unsplash.com/photo-1600566753190-17f0baa2a6c3?w=400&h=250&fit=crop",
        "duration": 15,
        "label": "Neighbourhood Drive",
    },
]


def add_videos():
    db: Session = SessionLocal()

    try:
        properties = db.query(Property).filter(Property.is_active == True).all()

        if not properties:
            print("No properties found in database. Run seed.py first.")
            return

        added = 0
        for idx, prop in enumerate(properties):
            # Check how many videos this property already has
            existing_count = db.query(PropertyVideo).filter(
                PropertyVideo.property_id == prop.id
            ).count()

            # Each property gets 2 videos (skip if it already has 2+)
            if existing_count >= 2:
                print(f"  - {prop.title}: already has {existing_count} videos, skipping")
                continue

            # Pick 2 videos for this property, cycling through the sample list
            videos_to_add = 2 - existing_count
            for v_idx in range(videos_to_add):
                sample = SAMPLE_VIDEOS[(idx * 2 + existing_count + v_idx) % len(SAMPLE_VIDEOS)]
                video = PropertyVideo(
                    id=str(uuid.uuid4()),
                    property_id=prop.id,
                    url=sample["url"],
                    streaming_url=sample["streaming_url"],
                    thumbnail_url=sample["thumbnail_url"],
                    title=f"{sample['label']} - {prop.title}",
                    description=f"Take a {sample['label'].lower()} of this property.",
                    duration=sample["duration"],
                    mime_type="video/mp4",
                    order=existing_count + v_idx,
                )
                db.add(video)
                added += 1
                print(f"  + {prop.title}: added \"{sample['label']}\"")

        db.commit()
        print(f"\nDone! Added {added} videos across {len(properties)} properties.")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    add_videos()
