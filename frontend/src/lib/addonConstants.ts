import { ADDON_REGISTRY, DASHBOARD_ADDONS as NEW_DASHBOARD_ADDONS } from './addonRegistry';

export const ADDON_TYPES = ADDON_REGISTRY.map(a => ({
    value: a.addon_type,
    label: a.name,
    logo: a.logo,
    color: a.color,
    description: a.description,
    has_dashboard: a.has_dashboard
}));

export const DASHBOARD_ADDONS = NEW_DASHBOARD_ADDONS;
