/**
 * CLI configuration — persists server URL + API token to ~/.smsly/config.json
 */

import Conf from 'conf';

const config = new Conf({
    projectName: 'smsly-cli',
    schema: {
        serverUrl: { type: 'string', default: '' },
        token: { type: 'string', default: '' },
    },
});

export function getConfig() {
    const serverUrl = config.get('serverUrl');
    const token = config.get('token');

    if (!serverUrl || !token) {
        return null;
    }

    // Also return any extra cached data (like lastPlan)
    return { ...config.store };
}

export function setConfig(data) {
    for (const [key, value] of Object.entries(data)) {
        config.set(key, value);
    }
}

export function clearConfig() {
    config.clear();
}
