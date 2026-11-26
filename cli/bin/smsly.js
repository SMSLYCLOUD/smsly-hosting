#!/usr/bin/env node

/**
 * SMSLY Hosting CLI
 *
 * Usage:
 *   smsly login <url>          — Connect to a SMSLY Hosting server
 *   smsly services [list]      — List all services
 *   smsly deploy <service>     — Trigger a deployment
 *   smsly logs <service>       — Fetch build logs
 *   smsly env <action> ...     — Manage environment variables
 *   smsly ecosystem <action>   — Scan & deploy entire GitHub ecosystem
 *   smsly tokens [list|create] — Manage API tokens
 */

import { Command } from 'commander';
import chalk from 'chalk';
import { getConfig, setConfig } from '../src/config.js';
import { api } from '../src/api.js';

const program = new Command();

const RESOURCE_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

function safeId(value, label = 'id') {
    const str = String(value ?? '').trim();
    if (!RESOURCE_ID_RE.test(str)) {
        throw new Error(`Invalid ${label}: must match ^[A-Za-z0-9_-]{1,128}$`);
    }
    return encodeURIComponent(str);
}

function normalizeServerUrl(rawUrl, allowInsecureHttp = false) {
    let candidate = String(rawUrl || '').trim().replace(/\/+$/, '');
    if (!candidate) {
        throw new Error('Server URL is required.');
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:\/|$))/.test(candidate) && !/^https?:\/\//i.test(candidate)) {
        throw new Error(`Unsupported URL scheme: ${candidate.split(':', 1)[0]}:`);
    }
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(candidate)) {
        candidate = `https://${candidate}`;
    }
    let parsed;
    try {
        parsed = new URL(candidate);
    } catch {
        throw new Error(`Invalid server URL: ${rawUrl}`);
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error(`Unsupported URL scheme: ${parsed.protocol}`);
    }
    const host = parsed.hostname.toLowerCase();
    const insecureAllowed = Boolean(allowInsecureHttp || process.env?.SMSLY_ALLOW_INSECURE_HTTP === '1');
    if (parsed.protocol === 'http:' && !LOOPBACK_HOSTS.has(host) && !insecureAllowed) {
        throw new Error(
            `Refusing plaintext http:// for non-loopback host "${host}". Use https:// or pass --allow-insecure-http (or set SMSLY_ALLOW_INSECURE_HTTP=1).`
        );
    }
    return candidate.replace(/\/+$/, '');
}

function validateNextPath(nextUrl, serverUrl) {
    const base = new URL(serverUrl);
    const parsed = new URL(String(nextUrl || ''), base);
    if (parsed.host !== base.host) {
        throw new Error(`Refusing cross-origin pagination URL: ${nextUrl}`);
    }
    const nextPath = `${parsed.pathname}${parsed.search}`;
    if (!nextPath.startsWith('/api/') || nextPath.startsWith('//')) {
        throw new Error(`Refusing unsafe pagination next path: ${nextPath}`);
    }
    return nextPath;
}

function loadValidatedConfig() {
    const config = getConfig();
    if (!config) {
        notLoggedIn();
        return null;
    }
    const allowInsecureHttp = Boolean(config.allowInsecureHttp ?? config.allow_insecure_http);
    const serverUrl = normalizeServerUrl(config.serverUrl || config.url, allowInsecureHttp);
    return { ...config, serverUrl, url: serverUrl, allowInsecureHttp };
}

function callApi(config, path, method = 'GET', body = null) {
    if (config?.allowInsecureHttp) {
        return api(config.serverUrl, config.token, path, method, body, { allowInsecureHttp: true });
    }
    if (body !== null && body !== undefined) {
        return api(config.serverUrl, config.token, path, method, body);
    }
    if (method !== 'GET') {
        return api(config.serverUrl, config.token, path, method);
    }
    return api(config.serverUrl, config.token, path);
}

async function promptSecret(promptText) {
    const readline = await import('node:readline');
    const { Writable } = await import('node:stream');
    let muted = false;
    const mutableStdout = new Writable({
        write(chunk, encoding, callback) {
            if (!muted) {
                process.stdout.write(chunk, encoding);
            }
            callback();
        },
    });
    const rl = readline.createInterface({
        input: process.stdin,
        output: mutableStdout,
        terminal: true,
    });
    return new Promise((resolve, reject) => {
        rl.on('SIGINT', () => {
            process.stdout.write('\n');
            rl.close();
            reject(new Error('Prompt cancelled'));
        });
        rl.question(promptText, (answer) => {
            process.stdout.write('\n');
            rl.close();
            resolve(String(answer || '').trim());
        });
        muted = true;
    });
}

async function fetchAllServices(serverUrl, token, allowInsecureHttp = false) {
    const collected = [];
    let nextPath = '/api/v1/services/?page_size=100';
    for (let page = 0; page < 50 && nextPath; page++) {
        const data = allowInsecureHttp
            ? await api(serverUrl, token, nextPath, 'GET', null, { allowInsecureHttp: true })
            : await api(serverUrl, token, nextPath);
        const items = Array.isArray(data) ? data : (data?.results || []);
        collected.push(...items);
        if (data && typeof data === 'object' && !Array.isArray(data) && data.next) {
            nextPath = validateNextPath(data.next, serverUrl);
        } else {
            nextPath = null;
        }
    }
    return collected;
}

program
    .name('smsly')
    .description(chalk.bold('SMSLY Hosting CLI') + ' — manage your cloud from the terminal')
    .version('1.0.0');

// ─── LOGIN ──────────────────────────────────────────────────────────────────
program
    .command('login <url>')
    .description('Connect to a SMSLY Hosting server')
    .option('-t, --token <token>', 'API token (will prompt if not provided)')
    .option('--allow-insecure-http', 'Allow plaintext http:// for non-loopback hosts')
    .action(async (url, opts) => {
        const ora = (await import('ora')).default;

        let serverUrl;
        try {
            serverUrl = normalizeServerUrl(url, opts.allowInsecureHttp);
        } catch (err) {
            console.log(chalk.red(`\n  ${err.message}\n`));
            process.exitCode = 1;
            return;
        }

        let token = String(opts.token || process.env?.SMSLY_TOKEN || '').trim();
        if (!token && process.stdin?.isTTY) {
            try {
                token = await promptSecret('API token: ');
            } catch {
                console.log(chalk.yellow('\nLogin cancelled.'));
                process.exitCode = 1;
                return;
            }
        }
        if (!token) {
            console.log(chalk.yellow('\nNo token provided.'));
            console.log(chalk.dim(`To generate a token, visit: ${serverUrl}/settings → API Tokens`));
            console.log(chalk.dim(`Then run: smsly login ${url} --token <your-token>\n`));
            process.exitCode = 1;
            return;
        }

        const spinner = ora('Connecting to server...').start();
        try {
            // Test the connection
            if (opts.allowInsecureHttp) {
                await api(serverUrl, token, '/api/v1/services/?page_size=1', 'GET', null, { allowInsecureHttp: true });
            } else {
                await api(serverUrl, token, '/api/v1/services/?page_size=1', 'GET');
            }
            setConfig({
                url: serverUrl,
                serverUrl,
                token,
                allowInsecureHttp: Boolean(opts.allowInsecureHttp),
            });
            spinner.succeed(chalk.green(`Connected to ${serverUrl}`));
            console.log(chalk.dim(`  Token saved to ~/.smsly/config.json.`));
        } catch (err) {
            spinner.fail(chalk.red(`Connection failed: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── SERVICES ───────────────────────────────────────────────────────────────
program
    .command('services')
    .description('List all services')
    .action(async () => {
        const config = loadValidatedConfig();
        if (!config) return;

        const ora = (await import('ora')).default;
        const spinner = ora('Fetching services...').start();
        try {
            const services = await fetchAllServices(config.serverUrl, config.token, config.allowInsecureHttp);
            spinner.stop();

            if (!services.length) {
                console.log(chalk.yellow('No services found.'));
                return;
            }

            console.log(chalk.bold(`\n  ${services.length} services:\n`));
            for (const svc of services) {
                const statusValue = svc.latest_deployment?.status || svc.status || 'UNKNOWN';
                const status = statusValue === 'ACTIVE'
                    ? chalk.green('● ACTIVE')
                    : statusValue === 'FAILED'
                        ? chalk.red('● FAILED')
                        : chalk.yellow(`● ${statusValue}`);
                console.log(`  ${status}  ${chalk.bold(svc.name)}  ${chalk.dim(svc.repository_url || '')}`);
            }
            console.log();
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── DEPLOY ─────────────────────────────────────────────────────────────────
program
    .command('deploy <service>')
    .description('Trigger a deployment for a service')
    .action(async (serviceName) => {
        const config = loadValidatedConfig();
        if (!config) return;

        const ora = (await import('ora')).default;
        const spinner = ora(`Deploying ${serviceName}...`).start();
        try {
            // First find the service
            const services = await fetchAllServices(config.serverUrl, config.token, config.allowInsecureHttp);
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());

            if (!svc) {
                spinner.fail(chalk.red(`Service "${serviceName}" not found.`));
                process.exitCode = 1;
                return;
            }

            const serviceId = safeId(svc.id, 'service_id');
            // Trigger deploy
            const result = await callApi(
                config,
                `/api/v1/services/${serviceId}/deploy/`, 'POST'
            );

            spinner.succeed(chalk.green(`Deployment queued for ${serviceName}`));
            if (result?.deployment_id) {
                console.log(chalk.dim(`  Deployment ID: ${result.deployment_id}`));
            }
        } catch (err) {
            spinner.fail(chalk.red(`Deploy failed: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── LOGS ───────────────────────────────────────────────────────────────────
program
    .command('logs <service>')
    .description('Fetch build logs for the latest deployment')
    .action(async (serviceName) => {
        const config = loadValidatedConfig();
        if (!config) return;

        const ora = (await import('ora')).default;
        const spinner = ora('Fetching logs...').start();
        try {
            // Find service
            const services = await fetchAllServices(config.serverUrl, config.token, config.allowInsecureHttp);
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                spinner.fail(chalk.red(`Service "${serviceName}" not found.`));
                process.exitCode = 1;
                return;
            }

            const serviceId = safeId(svc.id, 'service_id');
            // Get latest deployment
            const deploys = await callApi(config, `/api/v1/deployments/?service=${serviceId}`);
            const deployList = deploys?.results || deploys || [];
            const latest = deployList.find(d => d.service === svc.id) || deployList[0];

            spinner.stop();
            if (!latest) {
                console.log(chalk.yellow('No deployments found for this service.'));
                return;
            }

            const deployId = safeId(latest.id, 'deploy_id');
            let buildLogs = latest.build_logs || '';
            try {
                const logsData = await callApi(config, `/api/v1/deployments/${deployId}/build-logs/`);
                if (logsData?.build_logs !== undefined) {
                    buildLogs = logsData.build_logs || '';
                }
            } catch {
                // Fall back to latest.build_logs if build-logs endpoint is unavailable
            }

            console.log(chalk.bold(`\n  Deployment: ${latest.id}`));
            console.log(chalk.dim(`  Status: ${latest.status} | Created: ${latest.created_at}\n`));

            if (buildLogs) {
                console.log(buildLogs);
            } else {
                console.log(chalk.dim('  No build logs yet.'));
            }

            if (latest.ai_diagnosis) {
                console.log(chalk.cyan('\n  🤖 AI Diagnosis:\n'));
                console.log(`  ${latest.ai_diagnosis}\n`);
            }
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── ENV ────────────────────────────────────────────────────────────────────
const envCmd = program
    .command('env')
    .description('Manage environment variables');

envCmd
    .command('list <service>')
    .description('List env vars for a service')
    .action(async (serviceName) => {
        const config = loadValidatedConfig();
        if (!config) return;

        try {
            const services = await fetchAllServices(config.serverUrl, config.token, config.allowInsecureHttp);
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                console.log(chalk.red(`Service "${serviceName}" not found.`));
                process.exitCode = 1;
                return;
            }

            const serviceId = safeId(svc.id, 'service_id');
            // Get service detail which includes env_vars
            const detail = await callApi(config, `/api/v1/services/${serviceId}/`);
            const envVars = detail?.env_vars || [];

            if (!envVars.length) {
                console.log(chalk.yellow('No environment variables set.'));
                return;
            }

            console.log(chalk.bold(`\n  Environment variables for ${serviceName}:\n`));
            for (const ev of envVars) {
                const val = ev.is_secret ? chalk.dim('••••••••') : ev.value;
                console.log(`  ${chalk.cyan(ev.key)}=${val}`);
            }
            console.log();
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

envCmd
    .command('set <service> <pairs...>')
    .description('Set env vars (KEY=VALUE pairs)')
    .action(async (serviceName, pairs) => {
        const config = loadValidatedConfig();
        if (!config) return;

        try {
            const services = await fetchAllServices(config.serverUrl, config.token, config.allowInsecureHttp);
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                console.log(chalk.red(`Service "${serviceName}" not found.`));
                process.exitCode = 1;
                return;
            }

            const serviceId = safeId(svc.id, 'service_id');
            for (const pair of pairs) {
                const [key, ...rest] = pair.split('=');
                const value = rest.join('=');
                if (!key || !pair.includes('=')) {
                    console.log(chalk.red(`Invalid format: ${pair}. Use KEY=VALUE`));
                    process.exitCode = 1;
                    continue;
                }

                await callApi(
                    config,
                    `/api/v1/services/${serviceId}/env_vars/`, 'POST',
                    { key, value }
                );
                console.log(chalk.green(`  ✓ ${key} set`));
            }
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── ECOSYSTEM ──────────────────────────────────────────────────────────────
const ecoCmd = program
    .command('ecosystem')
    .description('Zero-config AI ecosystem deployment');

ecoCmd
    .command('scan')
    .description('Scan all GitHub repos and generate deploy plan')
    .action(async () => {
        const config = loadValidatedConfig();
        if (!config) return;

        const ora = (await import('ora')).default;
        const spinner = ora('Scanning GitHub repositories...').start();

        try {
            const { task_id, plan_id: initialPlanId } = await callApi(
                config,
                '/api/v1/cloud/ecosystem/scan/', 'POST'
            );
            const encodedTaskId = safeId(task_id, 'task_id');

            spinner.text = 'AI analyzing your ecosystem...';

            // Poll for result (bounded at ~10 minutes)
            let result;
            const maxAttempts = 200;
            for (let attempt = 0; ; attempt++) {
                if (attempt >= maxAttempts) {
                    spinner.fail(chalk.red('Scan timed out waiting for completion.'));
                    process.exitCode = 1;
                    return;
                }
                await new Promise(r => setTimeout(r, 3000));
                const status = await callApi(
                    config,
                    `/api/v1/cloud/ecosystem/task_status/?task_id=${encodedTaskId}`
                );

                if (status.status === 'SUCCESS') {
                    result = status.result;
                    break;
                } else if (status.status === 'FAILURE') {
                    spinner.fail(chalk.red(`Scan failed: ${status.error}`));
                    process.exitCode = 1;
                    return;
                }
            }

            spinner.succeed(chalk.green('Ecosystem scan complete!'));

            if (result.error) {
                console.log(chalk.red(`\n  Error: ${result.error}`));
                process.exitCode = 1;
                return;
            }

            console.log(chalk.bold(`\n  📊 Scan Results:`));
            console.log(chalk.dim(`  Repos scanned: ${result.total_repos_scanned || '?'}`));
            console.log(chalk.dim(`  Deployable: ${result.services?.length || 0}`));
            console.log(chalk.dim(`  AI Provider: ${result.ai_provider || '?'}\n`));

            if (result.services) {
                for (const svc of result.services) {
                    const skip = svc.skip ? chalk.dim(' [SKIP]') : '';
                    console.log(`  ${chalk.cyan(`#${svc.deploy_order}`)} ${chalk.bold(svc.name)} ${chalk.dim(`(${svc.stack}:${svc.port})`)}${skip}`);
                }
            }

            console.log(chalk.dim('\n  Run "smsly ecosystem deploy" to deploy this plan.\n'));

            // Persist only { plan_id } — never store env_vars/secrets on disk
            const planId = result?.plan_id || result?.id || initialPlanId;
            if (planId) {
                setConfig({ ...config, lastPlan: { plan_id: planId } });
            }
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

ecoCmd
    .command('deploy')
    .description('Deploy the last scanned ecosystem plan')
    .action(async () => {
        const config = loadValidatedConfig();
        if (!config) return;

        const plan = config.lastPlan;
        const planId = plan?.plan_id || plan?.id;
        if (!plan || !planId) {
            console.log(chalk.yellow('No cached plan. Run "smsly ecosystem scan" first.'));
            process.exitCode = 1;
            return;
        }

        const ora = (await import('ora')).default;
        const spinner = ora('Deploying ecosystem...').start();

        try {
            const planDetail = await callApi(
                config,
                `/api/v1/cloud/ecosystem/plans/${safeId(planId, 'plan_id')}/`
            );
            if (!planDetail?.plan || typeof planDetail.plan !== 'object') {
                throw new Error('Ecosystem plan detail does not contain a deployable plan.');
            }
            const deployPayload = { plan_id: planId, plan: planDetail.plan };
            const { task_id } = await callApi(
                config,
                '/api/v1/cloud/ecosystem/deploy/', 'POST', deployPayload
            );
            const encodedTaskId = safeId(task_id, 'task_id');

            // Poll (bounded at ~15 minutes)
            let result;
            const maxAttempts = 300;
            for (let attempt = 0; ; attempt++) {
                if (attempt >= maxAttempts) {
                    spinner.fail(chalk.red('Deploy poll timed out waiting for completion.'));
                    process.exitCode = 1;
                    return;
                }
                await new Promise(r => setTimeout(r, 3000));
                const status = await callApi(
                    config,
                    `/api/v1/cloud/ecosystem/task_status/?task_id=${encodedTaskId}`
                );

                if (status.status === 'SUCCESS') {
                    result = status.result;
                    break;
                } else if (status.status === 'FAILURE') {
                    spinner.fail(chalk.red(`Deploy failed: ${status.error}`));
                    process.exitCode = 1;
                    return;
                }
            }

            spinner.succeed(chalk.green('Ecosystem deployment queued!'));

            if (result.services) {
                for (const svc of result.services) {
                    const icon = svc.status === 'queued' ? chalk.green('✓')
                        : svc.status === 'skipped' ? chalk.yellow('⊘')
                            : chalk.red('✗');
                    console.log(`  ${icon} ${chalk.bold(svc.name)} — ${svc.status}`);
                }
                console.log(chalk.dim(`\n  Total: ${result.total} | Queued: ${result.queued} | Skipped: ${result.skipped}\n`));
            }

            // Clear cached plan
            const { lastPlan, ...cleanConfig } = config;
            setConfig(cleanConfig);
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
            process.exitCode = 1;
        }
    });

// ─── TOKENS ─────────────────────────────────────────────────────────────────
const tokensCmd = program
    .command('tokens')
    .description('Manage API tokens');

tokensCmd
    .command('list')
    .description('List all API tokens')
    .action(async () => {
        const config = loadValidatedConfig();
        if (!config) return;

        try {
            const data = await callApi(config, '/api/v1/tokens/');
            const tokens = data?.tokens || [];

            if (!tokens.length) {
                console.log(chalk.yellow('No tokens found.'));
                return;
            }

            console.log(chalk.bold(`\n  API Tokens:\n`));
            for (const t of tokens) {
                const active = t.is_active ? chalk.green('active') : chalk.red('revoked');
                const used = t.last_used_at ? `last used ${t.last_used_at}` : 'never used';
                console.log(`  ${chalk.cyan(t.prefix)}…  ${chalk.bold(t.name)}  [${active}]  ${chalk.dim(used)}`);
            }
            console.log();
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
            if (err.status === 403 || /\b403\b|forbidden|permission/i.test(err.message)) {
                console.log(chalk.yellow('  Hint: Scoped API tokens cannot manage account tokens. Use a full-access user token or manage tokens in Settings → API Keys.'));
            }
            process.exitCode = 1;
        }
    });

tokensCmd
    .command('create [name]')
    .description('Generate a new API token')
    .action(async (name) => {
        const config = loadValidatedConfig();
        if (!config) return;

        try {
            const data = await callApi(
                config,
                '/api/v1/tokens/create/', 'POST', { name: name || 'CLI Token' }
            );

            console.log(chalk.bold('\n  New API token created!\n'));
            console.log(chalk.green(`  ${data.token}\n`));
            console.log(chalk.yellow('  ⚠  Save this token — it won\'t be shown again.\n'));
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
            if (err.status === 403 || /\b403\b|forbidden|permission/i.test(err.message)) {
                console.log(chalk.yellow('  Hint: Scoped API tokens cannot create account tokens. Use a full-access user token or create tokens in Settings → API Keys.'));
            }
            process.exitCode = 1;
        }
    });

// ─── HELPERS ────────────────────────────────────────────────────────────────
function notLoggedIn() {
    console.log(chalk.red('\n  Not logged in. Run: smsly login <server-url> --token <api-token>\n'));
    process.exitCode = 1;
}

program.parse();
