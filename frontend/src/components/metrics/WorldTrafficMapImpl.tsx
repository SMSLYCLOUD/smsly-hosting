'use client';

import React, { useRef, useEffect } from 'react';
import mapboxgl from 'mapbox-gl';

interface CountryTraffic {
    code: string;
    name: string;
    count: number;
    percentage: number;
    unique_ips: number;
}

const COUNTRY_CENTERS: Record<string, [number, number]> = {
    US: [-95.7129, 37.0902], GB: [-3.436, 55.3781], DE: [10.4515, 51.1657],
    FR: [2.2137, 46.2276], CA: [-106.3468, 56.1304], AU: [133.7751, -25.2744],
    JP: [138.2529, 36.2048], IN: [78.9629, 20.5937], BR: [-51.9253, -14.235],
    NL: [5.2913, 52.1326], SG: [103.8198, 1.3521], NG: [8.6753, 9.082],
    ZA: [22.9375, -30.5595], KR: [127.7669, 35.9078], MX: [-102.5528, 23.6345],
    SE: [18.6435, 60.1282], IT: [12.5674, 41.8719], ES: [-3.7038, 40.4168],
    PL: [19.1451, 51.9194], CH: [8.2275, 46.8182], NO: [8.4689, 60.472],
    DK: [9.5018, 56.2639], FI: [25.7482, 61.9241], PT: [-8.2245, 39.3999],
    BE: [4.4699, 50.5039], AT: [14.5501, 47.5162], IE: [-8.2437, 53.4129],
    NZ: [174.886, -40.9006], AR: [-63.6167, -38.4161], CL: [-71.543, -35.6751],
    CO: [-74.2973, 4.5709], PE: [-75.0152, -9.19], VN: [108.2772, 14.0583],
    TH: [100.9925, 15.87], PH: [121.774, 12.8797], ID: [113.9213, -0.7893],
    MY: [101.9758, 4.2105], PK: [69.3451, 30.3753], BD: [90.3563, 23.685],
    EG: [30.8025, 26.8206], KE: [37.9062, 1.2921], GH: [-1.0232, 7.9465],
    UA: [31.1656, 48.3794], RO: [24.9668, 45.9432], CZ: [15.473, 49.8175],
    GR: [21.8243, 39.0742], TR: [35.2433, 38.9637], IL: [34.8516, 31.0461],
    AE: [53.8478, 23.4241], SA: [45.0792, 23.8859], HK: [114.1095, 22.3964],
    TW: [120.9605, 23.6978],
};

interface Props {
    token: string;
    countries: CountryTraffic[];
    totalRequests: number;
}

export default function WorldTrafficMapImpl({ token, countries, totalRequests }: Props) {
    const mapContainer = useRef<HTMLDivElement>(null);
    const map = useRef<mapboxgl.Map | null>(null);
    const markers = useRef<mapboxgl.Marker[]>([]);

    useEffect(() => {
        if (!mapContainer.current || map.current) return;

        mapboxgl.accessToken = token;
        map.current = new mapboxgl.Map({
            container: mapContainer.current,
            style: 'mapbox://styles/mapbox/dark-v11',
            center: [20, 20],
            zoom: 1.5,
            attributionControl: false,
            interactive: true,
        });

        map.current.addControl(new mapboxgl.NavigationControl(), 'top-right');

        return () => {
            markers.current.forEach(m => m.remove());
            markers.current = [];
            map.current?.remove();
            map.current = null;
        };
    }, [token]);

    useEffect(() => {
        if (!map.current) return;

        markers.current.forEach(m => m.remove());
        markers.current = [];

        const maxCount = Math.max(...countries.map(c => c.count), 1);

        countries.forEach(country => {
            const center = COUNTRY_CENTERS[country.code];
            if (!center) return;

            const size = Math.max(8, (country.count / maxCount) * 40);
            const opacity = 0.3 + (country.count / maxCount) * 0.7;

            const el = document.createElement('div');
            el.style.cssText = `
                width: ${size}px;
                height: ${size}px;
                border-radius: 50%;
                background: rgba(6, 182, 212, ${opacity});
                border: 2px solid rgba(34, 211, 238, 0.5);
                box-shadow: 0 0 ${size}px rgba(6, 182, 212, 0.3);
                cursor: pointer;
                transition: transform 0.2s;
            `;

            const popup = new mapboxgl.Popup({
                offset: 15,
                closeButton: false,
            }).setHTML(`
                <div style="padding: 4px 0; font-size: 13px; color: #fff;">
                    <strong>${country.name}</strong><br/>
                    ${country.count.toLocaleString()} requests (${country.percentage}%)<br/>
                    ${country.unique_ips} unique IPs
                </div>
            `);

            const marker = new mapboxgl.Marker({ element: el })
                .setLngLat(center)
                .setPopup(popup)
                .addTo(map.current!);

            markers.current.push(marker);
        });
    }, [countries]);

    return (
        <div ref={mapContainer} className="rounded-lg overflow-hidden" style={{ width: '100%', height: 400 }} />
    );
}
