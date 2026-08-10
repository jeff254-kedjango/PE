// src/components/map/PropertyLocationMap.tsx
// Small embedded Leaflet map for the PropertyDetails location section.
// Shows a single marker at the property's coordinates with an address popup.

import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

interface PropertyLocationMapProps {
  latitude: number;
  longitude: number;
  address: string;
}

const PropertyLocationMap: React.FC<PropertyLocationMapProps> = ({ latitude, longitude, address }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [latitude, longitude],
      zoom: 15,
      zoomControl: false,
      scrollWheelZoom: false,
      dragging: true,
      attributionControl: false,
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
    }).addTo(map);

    L.marker([latitude, longitude])
      .addTo(map)
      .bindPopup(`<strong>${address}</strong>`, { maxWidth: 220 })
      .openPopup();

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [latitude, longitude, address]);

  return <div ref={containerRef} className="pd-location__map" />;
};

export default PropertyLocationMap;
