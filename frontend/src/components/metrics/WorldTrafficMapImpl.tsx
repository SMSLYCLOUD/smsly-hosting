'use client';

import React, { useRef, useEffect } from 'react';
import maplibregl from 'maplibre-gl';

interface CountryTraffic {
    code: string;
    name: string;
    count: number;
    percentage: number;
    unique_ips: number;
    latitude?: number | null;
    longitude?: number | null;
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
    // Additional countries
    RU: [105.3188, 61.524], CN: [104.1954, 35.8617], LA: [102.4955, 19.8563],
    MM: [95.956, 21.9162], KH: [104.9903, 12.5657], NP: [84.124, 28.3949],
    LK: [80.7718, 7.8731],
    AF: [67.7099, 33.9391], IR: [53.688, 32.4279], IQ: [43.6793, 33.2232],
    SY: [38.9968, 34.8021], JO: [36.2384, 30.5852], KW: [47.4818, 29.3117],
    BH: [50.5577, 26.0667], QA: [51.1789, 25.3548], OM: [55.9754, 21.4735],
    YE: [48.5164, 15.5527], UZ: [64.5853, 41.3775], KZ: [66.9237, 48.0196],
    MN: [103.8467, 46.8625], GE: [43.3569, 42.3154], AM: [45.0382, 40.0691],
    AZ: [47.5769, 40.1431], CY: [33.4299, 35.1264], MT: [14.3754, 35.9375],
    IS: [-19.0208, 64.9631], LU: [6.1296, 49.8153], LV: [24.6032, 56.8796],
    LT: [23.8813, 55.1694], EE: [25.0136, 58.5953], SK: [19.699, 48.669],
    HU: [19.5033, 47.1625], BG: [25.4858, 42.7339], RS: [20.9114, 44.0165],
    HR: [15.2, 45.1], BA: [17.6791, 43.9159], SI: [14.9955, 46.1512],
    ME: [19.3744, 42.7087], AL: [20.1683, 41.1533], MK: [21.7453, 41.5122],
    XK: [20.902, 42.6026], MA: [-7.0926, 31.7917], DZ: [1.6596, 28.0339],
    TN: [9.5375, 33.8869], LY: [17.2283, 26.3351], SD: [30.2176, 12.8628],
    ET: [40.4897, 9.145], TZ: [34.8888, -6.369], UG: [32.2903, 1.3733],
    RW: [29.8739, -1.9403], CM: [12.3547, 7.3697], CI: [-5.5471, 7.54],
    SN: [-14.4524, 14.4974], ML: [-3.9962, 17.5707], NE: [8.0815, 17.6078],
    TD: [18.7322, 15.4542], BF: [-1.5616, 12.3714], GN: [-11.7455, 9.9456],
    SL: [-11.7797, 8.4606], LR: [-9.4295, 6.4281], GQ: [10.497, 1.6508],
    GA: [11.6094, -0.8037], CG: [15.8277, -0.228], CD: [21.7587, -4.0383],
    AO: [17.8739, -11.2027], ZM: [28.3228, -13.1339], ZW: [29.1549, -19.0154],
    BW: [24.6282, -22.3285], NA: [18.4904, -22.5609], MZ: [35.5296, -18.6687],
    MG: [46.8691, -18.7669], MU: [57.5522, -20.3484],     RE: [55.5364, -21.1151],
    CV: [-24.0084, 16.5388], ST: [6.6131, 0.1864],
    PW: [134.5825, 7.515], FJ: [179.4144, -16.578],
    PG: [147.1925, -6.315], SB: [160.1562, -9.6195], VU: [166.9592, -15.3767],
    WS: [-172.1046, -13.759], TO: [-175.1982, -21.179], KI: [173.0297, 1.8709],
    MH: [171.1845, 7.1315], FM: [158.2501, 6.8822], NR: [166.9315, -0.5228],
    TV: [179.194, -7.1095], CK: [-159.7777, -21.2367], NU: [-169.8672, -19.0544],
    TK: [-171.848, -8.9674], GF: [-53.1258, 3.9339], SR: [-56.0278, 3.9193],
    GY: [-58.9302, 4.8604], EC: [-78.1834, -1.8312], BO: [-63.5887, -16.2902],
    PY: [-58.4438, -23.4425], UY: [-55.7658, -32.5228], CR: [-83.7534, 9.7489],
    PA: [-80.7821, 8.538], NI: [-85.2072, 12.8654], HN: [-87.243, 15.2],
    GT: [-90.5069, 15.7835], BZ: [-88.4976, 17.1899], SV: [-88.8965, 13.7942],
    CU: [-77.7812, 21.5218], JM: [-77.2975, 18.1096], HT: [-72.2856, 18.9712],
    DO: [-70.1667, 18.7357], TT: [-61.2225, 10.6918], BB: [-59.5432, 13.1939],
    BS: [-77.3963, 25.0343], BM: [-64.7505, 32.3078], KY: [-80.5667, 19.3133],
    AW: [-70.0167, 12.523], VG: [-64.6333, 18.4167], VI: [-64.9378, 18.3358],
    PR: [-66.5901, 18.2208], GP: [-61.551, 16.265],     MQ: [-61.1667, 14.6417],
    BL: [-63.783, 17.9], MF: [-63.0723, 18.0924],
    PM: [-56.2711, 46.8243], GL: [-42.6043, 71.7069], FO: [-6.9118, 61.8926],
    SJ: [15.472, 78.2232], AX: [19.9453, 60.1785], AD: [1.5218, 42.5063],
    MC: [7.4246, 43.7384], LI: [9.5554, 47.166], SM: [12.4534, 43.9424],
    VA: [12.4534, 41.9029], GG: [-2.5853, 49.45], JE: [-2.0987, 49.2133],
    IM: [-4.5481, 54.2361],
};

// OpenFreeMap free tile styles (no API key required)
const STYLES = {
    dark: 'https://tiles.openfreemap.org/styles/liberty',
    bright: 'https://tiles.openfreemap.org/styles/bright',
    positron: 'https://tiles.openfreemap.org/styles/positron',
};

interface Props {
    token: string;
    countries: CountryTraffic[];
    totalRequests: number;
}

export default function WorldTrafficMapImpl({ token, countries, totalRequests }: Props) {
    const mapContainer = useRef<HTMLDivElement>(null);
    const map = useRef<maplibregl.Map | null>(null);
    const markers = useRef<maplibregl.Marker[]>([]);

    useEffect(() => {
        if (!mapContainer.current || map.current) return;

        // Use Mapbox style if token provided, otherwise use free OpenFreeMap tiles
        const mapboxStyle = token
            ? `https://api.mapbox.com/styles/v1/mapbox/dark-v11?access_token=${token}`
            : null;
        const styleUrl = mapboxStyle || STYLES.dark;

        const m = new maplibregl.Map({
            container: mapContainer.current,
            style: styleUrl,
            center: [20, 20],
            zoom: 1.5,
            attributionControl: false,
            interactive: true,
        });

        if (mapboxStyle) {
            m.on('error', (e) => {
                console.warn('Map tile error:', e.error?.message || e);
                console.warn('Mapbox token invalid or expired, falling back to OpenFreeMap');
                try { m.setStyle(STYLES.dark); } catch {}
            });
        }

        m.addControl(new maplibregl.NavigationControl(), 'top-right');
        map.current = m;

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
            const center: [number, number] | undefined =
                COUNTRY_CENTERS[country.code] ||
                (country.longitude != null && country.latitude != null
                    ? [country.longitude, country.latitude]
                    : undefined);
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

            const popup = new maplibregl.Popup({
                offset: 15,
                closeButton: false,
            }).setHTML(`
                <div style="padding: 4px 0; font-size: 13px; color: #fff;">
                    <strong>${country.name}</strong><br/>
                    ${country.count.toLocaleString()} requests (${country.percentage}%)<br/>
                    ${country.unique_ips} unique IPs
                </div>
            `);

            const marker = new maplibregl.Marker({ element: el })
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
