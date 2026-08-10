# Frontend Integration Guide - Weespas Backend v2.0

## 🔗 How the Frontend Uses the New Backend

This guide explains how the React frontend integrates with the redesigned backend API.

---

## 🌐 API Base URL

```typescript
const API_BASE_URL = "http://localhost:8000/api/v1";

// Usage
const response = await fetch(`${API_BASE_URL}/properties?skip=0&limit=20`);
```

---

## 📋 API Endpoints Overview

### Properties List (with Pagination)

**GET** `/properties?skip=0&limit=20`

```typescript
// Response
{
  "total": 1234,
  "skip": 0,
  "limit": 20,
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "3-Bedroom House in Westlands",
      "price": 8500000,
      "currency": "KES",
      "listing_type": "sale",
      "category": "house",
      "location_name": "Westlands, Nairobi",
      "latitude": -1.2662,
      "longitude": 36.8142,
      "agent_name": "John Kipchoge",
      "main_image": {
        "id": "...",
        "url": "https://...",
        "thumbnail_url": "https://...",
        "alt_text": "..."
      },
      "is_featured": true,
      "view_count": 254,
      "created_at": "2026-04-09T10:30:00"
    }
  ]
}
```

### Get Property Details

**GET** `/properties/{id}`

```typescript
// Response (full detail model)
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "3-Bedroom House in Westlands",
  "description": "Beautiful modern house with spacious garden...",
  "price": 8500000,
  "currency": "KES",
  "listing_type": "sale",
  "category": "house",
  "is_engineer_certified": true,
  "bedrooms": 3,
  "bathrooms": 2,
  "size": "300 sqft",
  "parking_spaces": 2,
  "year_built": 2020,
  
  "address": {
    "id": "...",
    "location_name": "Westlands, Nairobi",
    "street_address": "...",
    "city": "Nairobi",
    "county": "Nairobi County",
    "country": "Kenya",
    "latitude": -1.2662,
    "longitude": 36.8142
  },
  
  "agent": {
    "id": "...",
    "agent_name": "John Kipchoge",
    "agent_phone_number": "+254713083378",
    "agent_profile_picture": "https://...",
    "email": "john@weespas.com",
    "bio": "Experienced agent...",
    "is_verified": true
  },
  
  "images": [
    {
      "id": "...",
      "url": "https://...",
      "thumbnail_url": "https://...",
      "alt_text": "House exterior",
      "order": 0,
      "is_main": true,
      "created_at": "2026-04-09T10:30:00"
    }
  ],
  
  "videos": [
    {
      "id": "...",
      "url": "https://...",
      "streaming_url": "https://....m3u8",  // HLS stream
      "title": "Virtual Tour",
      "description": "Complete walkthrough",
      "duration": 120,
      "thumbnail_url": "https://..."
    }
  ],
  
  "is_active": true,
  "is_featured": true,
  "view_count": 254,
  "created_at": "2026-04-09T10:30:00",
  "updated_at": "2026-04-09T14:20:00",
  "expires_at": null
}
```

### Nearby Properties (Geo-Search)

**GET** `/properties/nearby?latitude={lat}&longitude={lon}&radius={km}&skip=0&limit=20`

```typescript
// Example: Properties within 5km of point
fetch('/api/v1/properties/nearby?latitude=-1.2833&longitude=36.8167&radius=5&skip=0&limit=20')

// Response: Same format as properties list
{
  "total": 42,
  "items": [
    {
      "id": "...",
      "title": "...",
      "distance": 2.3  // Distance in km
    }
  ]
}
```

### Advanced Filtering

**POST** `/properties/filter`

```typescript
// Request body
{
  "skip": 0,
  "limit": 20,
  "latitude": -1.2833,
  "longitude": 36.8167,
  "radius": 10,          // km
  "listing_type": "sale",
  "category": "apartment",
  "min_price": 3000000,
  "max_price": 10000000,
  "engineer_certified": true,
  "bedrooms": 2,
  "sort_by": "price",    // or "distance", "created_at"
  "sort_order": "asc"    // or "desc"
}

// Response: List format with filters applied
{
  "total": 12,
  "items": [...]
}
```

### Search Properties

**GET** `/properties/search/query?q={search_term}&skip=0&limit=20`

```typescript
// Example: Search for "villa" in title/description
fetch('/api/v1/properties/search/query?q=villa&skip=0&limit=20')

// Response: List format with matching results
```

### Featured Properties

**GET** `/properties/featured?limit=10`

```typescript
// Response: Array of featured properties
[
  {
    "id": "...",
    "title": "Luxury Villa in Karen",
    "price": 25000000,
    ...
  }
]
```

### Create Property (Admin/Agent)

**POST** `/properties`

```typescript
// Request body
{
  "title": "Modern House",
  "description": "Beautiful property with garden",
  "price": 8500000,
  "currency": "KES",
  "listing_type": "sale",
  "category": "house",
  "location_name": "Westlands, Nairobi",
  "latitude": -1.2662,
  "longitude": 36.8142,
  "bedrooms": 3,
  "bathrooms": 2,
  "is_engineer_certified": true,
  "agent_id": "550e8400-e29b-41d4-a716-446655440000"
}

// Response: Full property object with ID
{
  "id": "new-uuid",
  "title": "Modern House",
  ...
}
```

---

## 🎯 Frontend Implementation Examples

### 1. Fetch & Display Properties List

```typescript
// useProperties hook (already exists - needs updating)
import { useState, useEffect } from 'react';

interface Property {
  id: string;
  title: string;
  price: number;
  currency: string;
  location_name: string;
  main_image?: {
    url: string;
    thumbnail_url: string;
  };
  listing_type: string;
}

export function useProperties() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchProperties = async () => {
      try {
        const response = await fetch(
          'http://localhost:8000/api/v1/properties?skip=0&limit=20'
        );
        const data = await response.json();
        setProperties(data.items);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchProperties();
  }, []);

  return { properties, loading, error };
}
```

### 2. Geo-Search (Nearby Properties)

```typescript
export function useNearbyProperties(latitude: number, longitude: number, radius: number = 10) {
  const [properties, setProperties] = useState<Property[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchNearby = async () => {
      setLoading(true);
      try {
        const response = await fetch(
          `http://localhost:8000/api/v1/properties/nearby?latitude=${latitude}&longitude=${longitude}&radius=${radius}&skip=0&limit=50`
        );
        const data = await response.json();
        setProperties(data.items);
      } catch (err) {
        console.error('Geo-search failed:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchNearby();
  }, [latitude, longitude, radius]);

  return { properties, loading };
}
```

### 3. Advanced Filtering

```typescript
export async function filterProperties(filters: FilterParams) {
  const response = await fetch('http://localhost:8000/api/v1/properties/filter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(filters)
  });

  const data = await response.json();
  return data;
}

// Usage
const results = await filterProperties({
  listing_type: 'sale',
  category: 'apartment',
  min_price: 3000000,
  max_price: 10000000,
  bedrooms: 2,
  latitude: -1.2833,
  longitude: 36.8167,
  radius: 5,
  sort_by: 'price',
  sort_order: 'asc'
});
```

### 4. Fetch Property Details

```typescript
export async function getPropertyDetails(propertyId: string) {
  const response = await fetch(
    `http://localhost:8000/api/v1/properties/${propertyId}`
  );
  
  if (!response.ok) throw new Error('Property not found');
  
  return response.json();
}

// Usage in PropertyDetails component
useEffect(() => {
  if (propertyId) {
    getPropertyDetails(propertyId)
      .then(setProperty)
      .catch(handleError);
  }
}, [propertyId]);
```

### 5. Display Images with Responsive Loading

```typescript
// Use thumbnail_url for lazy loading, url for full-size
function PropertyImage({ image }: { image: PropertyImageType }) {
  return (
    <img
      src={image.thumbnail_url}    // Low-res for initial load
      data-src={image.url}         // Full-res for display
      alt={image.alt_text}
      loading="lazy"               // Native lazy loading
      onLoad={(e) => {
        e.currentTarget.src = image.url;  // Swap to full-res
      }}
    />
  );
}
```

### 6. Video Player (HLS Streaming)

```typescript
// Use HLS.js for adaptive bitrate streaming
import Hls from 'hls.js';

function PropertyVideo({ video }: { video: PropertyVideoType }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (video.streaming_url && videoRef.current) {
      const hls = new Hls();
      hls.loadSource(video.streaming_url);
      hls.attachMedia(videoRef.current);
      return () => hls.destroy();
    }
  }, [video.streaming_url]);

  return (
    <video
      ref={videoRef}
      controls
      poster={video.thumbnail_url}
      style={{ width: '100%' }}
    />
  );
}
```

---

## 🔑 Key Integration Points

### For useProperties Hook

Current implementation gets all properties. Update to support:
- ✅ Pagination (`skip`, `limit`)
- ✅ Filtering (`listing_type`, `category`, `price_range`)
- ✅ Sorting (`sort_by`, `sort_order`)

### For useNearbyProperties Hook

Already exists but needs updating for:
- ✅ Returning distance from API
- ✅ Proper type mapping
- ✅ Error handling

### For Search Component

Add support for:
- ✅ Full-text search endpoint
- ✅ Category filtering
- ✅ Price range filtering
- ✅ Bedroom filtering

---

## 📊 Data Type Mapping

### Backend Enums → Frontend

```typescript
listing_type: "sale" | "rent"
category: "house" | "apartment" | "villa" | "studio" | "office" | "land" | "warehouse" | "shop" | "kiosk"

// TypeScript types
type ListingType = 'sale' | 'rent';
type PropertyCategory = 'house' | 'apartment' | 'villa' | ... ;
```

### Price Formatting

```typescript
// Show prices with formatting
const formatPrice = (price: number, currency: string = 'KES') => {
  return new Intl.NumberFormat('en-KE', {
    style: 'currency',
    currency: currency
  }).format(price);
};

// Example: 8500000 → "KES 8,500,000"
```

### Distance Display

```typescript
const formatDistance = (km: number) => {
  if (km < 1) return `${(km * 1000).toFixed(0)}m`;
  return `${km.toFixed(1)}km`;
};
```

---

## ⚠️ Error Handling

```typescript
async function apiCall<T>(url: string): Promise<T> {
  try {
    const response = await fetch(url);
    
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error('Resource not found');
      }
      if (response.status === 400) {
        throw new Error('Invalid request parameters');
      }
      throw new Error(`API error: ${response.statusText}`);
    }
    
    return response.json();
  } catch (error) {
    console.error('API call failed:', error);
    throw error;
  }
}
```

---

## 🔄 Pagination Pattern

```typescript
interface PaginatedResponse<T> {
  total: number;
  skip: number;
  limit: number;
  items: T[];
}

// Infinite scroll implementation
useEffect(() => {
  if (isNearBottom) {
    loadMore();
  }
}, [isNearBottom]);

const loadMore = async () => {
  const newData = await fetch(
    `/properties?skip=${items.length}&limit=20`
  );
  setItems(prev => [...prev, ...newData.items]);
};
```

---

## 🎨 UI Component Integration

### PropertyCard Component

```typescript
function PropertyCard({ property }: { property: PropertyListResponse }) {
  return (
    <div className="property-card">
      <img 
        src={property.main_image?.thumbnail_url}
        alt={property.title}
        loading="lazy"
      />
      <h3>{property.title}</h3>
      <p className="price">{formatPrice(property.price)}</p>
      <p className="location">{property.location_name}</p>
      {property.distance && (
        <p className="distance">📍 {formatDistance(property.distance)}</p>
      )}
      {property.is_engineer_certified && (
        <badge>✓ Engineer Certified</badge>
      )}
      {property.is_featured && (
        <badge className="featured">★ Featured</badge>
      )}
    </div>
  );
}
```

### Filters Component

```typescript
function Filters() {
  const [filters, setFilters] = useState({
    listing_type: 'sale',
    category: undefined,
    min_price: undefined,
    max_price: undefined,
    bedrooms: undefined
  });

  const handleFilter = async () => {
    const results = await filterProperties(filters);
    setResults(results);
  };

  return (
    <form onSubmit={handleFilter}>
      <select 
        value={filters.listing_type}
        onChange={(e) => setFilters({...filters, listing_type: e.target.value as any})}
      >
        <option value="sale">For Sale</option>
        <option value="rent">For Rent</option>
      </select>
      
      <select 
        value={filters.category || ''}
        onChange={(e) => setFilters({...filters, category: e.target.value})}
      >
        <option value="">All Categories</option>
        <option value="apartment">Apartment</option>
        <option value="house">House</option>
        <option value="villa">Villa</option>
        {/* ... */}
      </select>
      
      <input 
        type="number" 
        placeholder="Min Price"
        onChange={(e) => setFilters({...filters, min_price: Number(e.target.value)})}
      />
      
      <input 
        type="number" 
        placeholder="Max Price"
        onChange={(e) => setFilters({...filters, max_price: Number(e.target.value)})}
      />
      
      <button type="submit">Apply Filters</button>
    </form>
  );
}
```

---

## 📱 Mobile Considerations

1. **Image Optimization**
   - Use `thumbnail_url` for list views
   - Lazy load with IntersectionObserver
   - Responsive image sizes

2. **Pagination**
   - Infinite scroll for mobile
   - Button pagination for desktop

3. **Filters**
   - Bottom sheet for mobile
   - Sidebar for desktop

---

## 🚀 Performance Tips

1. **Cache Results**
   ```typescript
   const cache = new Map();
   const getCachedProperty = (id: string) => {
     if (!cache.has(id)) {
       cache.set(id, getPropertyDetails(id));
     }
     return cache.get(id);
   };
   ```

2. **Request Debouncing**
   ```typescript
   const debouncedSearch = debounce(
     (query) => searchProperties(query),
     300
   );
   ```

3. **Virtual Scrolling**
   ```typescript
   import { FixedSizeList } from 'react-window';
   // For large lists
   ```

---

## 📞 API Base Configuration

Update your API configuration:

```typescript
// api/config.ts
export const WEESPAS_API = {
  BASE_URL: process.env.REACT_APP_API_URL || 'http://localhost:8000',
  VERSION: 'v1',
  TIMEOUT: 30000
};

export const getApiUrl = (endpoint: string) => {
  return `${WEESPAS_API.BASE_URL}/api/${WEESPAS_API.VERSION}${endpoint}`;
};
```

---

## 📊 Analytics & Stats Dashboard

The `/stats` page consumes six analytics endpoints documented in
[`ANALYTICS_USECASES.md`](./ANALYTICS_USECASES.md):

- `GET /agents/me/stats` — agent-scoped totals (active / inactive / featured / views).
- `GET /analytics/summary` — site-wide counters for the strip.
- `GET /analytics/categories` — category interest ranking.
- `GET /analytics/prices` — engagement-weighted price histogram (sale + rent).
- `GET /analytics/heatmap/access` — where users access from (county / city).
- `GET /analytics/heatmap/interest` — where users are looking (Address-grouped + raw search points).

All `/analytics/*` endpoints require `agent` / `staff` / `admin` role. See
`ANALYTICS_USECASES.md` for response shapes, query params, score formulas and
known limitations.

### Seeding for demo

```bash
python add_analytics.py
python seed.py
python seed_expanded.py
python seed_stats.py
python seed_analytics_edges.py
```

Run in this order — `seed_stats.py` requires properties to exist, and
`seed_analytics_edges.py` requires properties + agents to attach events to.
`seed_analytics_edges.py` is idempotent; `seed_expanded.py` is not.

---

**Summary**: The new backend provides a comprehensive API that supports paginated lists, advanced filtering, geo-spatial searches, and rich media. The frontend can leverage these capabilities to build a premium user experience with efficient data loading and responsive interactions.
