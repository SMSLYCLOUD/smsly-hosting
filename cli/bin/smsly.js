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

program
    .name('smsly')
    .description(chalk.bold('SMSLY Hosting CLI') + ' — manage your cloud from the terminal')
    .version('1.0.0');

// ─── LOGIN ──────────────────────────────────────────────────────────────────
program
    .command('login <url>')
    .description('Connect to a SMSLY Hosting server')
    .option('-t, --token <token>', 'API token (will prompt if not provided)')
    .action(async (url, opts) => {
        const ora = (await import('ora')).default;

        // Normalize URL
        let serverUrl = url.replace(/\/+$/, '');
        if (!serverUrl.startsWith('http')) {
            serverUrl = `https://${serverUrl}`;
        }

        let token = opts.token;
        if (!token) {
            // If no token, try to create one via existing session
            console.log(chalk.yellow('\nNo token provided.'));
            console.log(chalk.dim(`To generate a token, visit: ${serverUrl}/settings → API Tokens`));
            console.log(chalk.dim(`Then run: smsly login ${url} --token <your-token>\n`));
            process.exit(1);
        }

        const spinner = ora('Connecting to server...').start();
        try {
            // Test the connection
            const data = await api(serverUrl, token, '/api/v1/services/', 'GET');
            setConfig({ serverUrl, token });
            spinner.succeed(chalk.green(`Connected to ${serverUrl}`));
            console.log(chalk.dim(`  Token saved to config.`));
        } catch (err) {
            spinner.fail(chalk.red(`Connection failed: ${err.message}`));
            process.exit(1);
        }
    });

// ─── SERVICES ───────────────────────────────────────────────────────────────
program
    .command('services')
    .description('List all services')
    .action(async () => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        const ora = (await import('ora')).default;
        const spinner = ora('Fetching services...').start();
        try {
            const data = await api(config.serverUrl, config.token, '/api/v1/services/');
            spinner.stop();

            const services = data.results || data;
            if (!services.length) {
                console.log(chalk.yellow('No services found.'));
                return;
            }

            console.log(chalk.bold(`\n  ${services.length} services:\n`));
            for (const svc of services) {
                const status = svc.status === 'ACTIVE'
                    ? chalk.green('● ACTIVE')
                    : svc.status === 'FAILED'
                        ? chalk.red('● FAILED')
                        : chalk.yellow(`● ${svc.status}`);
                console.log(`  ${status}  ${chalk.bold(svc.name)}  ${chalk.dim(svc.repository_url || '')}`);
            }
            console.log();
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
        }
    });

// ─── DEPLOY ─────────────────────────────────────────────────────────────────
program
    .command('deploy <service>')
    .description('Trigger a deployment for a service')
    .action(async (serviceName) => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        const ora = (await import('ora')).default;
        const spinner = ora(`Deploying ${serviceName}...`).start();
        try {
            // First find the service
            const data = await api(config.serverUrl, config.token, '/api/v1/services/');
            const services = data.results || data;
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());

            if (!svc) {
                spinner.fail(chalk.red(`Service "${serviceName}" not found.`));
                return;
            }

            // Trigger deploy
            const result = await api(
                config.serverUrl, config.token,
                `/api/v1/services/${svc.id}/deploy/`, 'POST'
            );

            spinner.succeed(chalk.green(`Deployment queued for ${serviceName}`));
            if (result.deployment_id) {
                console.log(chalk.dim(`  Deployment ID: ${result.deployment_id}`));
            }
        } catch (err) {
            spinner.fail(chalk.red(`Deploy failed: ${err.message}`));
        }
    });

// ─── LOGS ───────────────────────────────────────────────────────────────────
program
    .command('logs <service>')
    .description('Fetch build logs for the latest deployment')
    .action(async (serviceName) => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        const ora = (await import('ora')).default;
        const spinner = ora('Fetching logs...').start();
        try {
            // Find service
            const data = await api(config.serverUrl, config.token, '/api/v1/services/');
            const services = data.results || data;
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                spinner.fail(chalk.red(`Service "${serviceName}" not found.`));
                return;
            }

            // Get latest deployment
            const deploys = await api(config.serverUrl, config.token, '/api/v1/deployments/');
            const deployList = deploys.results || deploys;
            const latest = deployList.find(d => d.service === svc.id);

            spinner.stop();
            if (!latest) {
                console.log(chalk.yellow('No deployments found for this service.'));
                return;
            }

            console.log(chalk.bold(`\n  Deployment: ${latest.id}`));
            console.log(chalk.dim(`  Status: ${latest.status} | Created: ${latest.created_at}\n`));

            if (latest.build_logs) {
                console.log(latest.build_logs);
            } else {
                console.log(chalk.dim('  No build logs yet.'));
            }

            if (latest.ai_diagnosis) {
                console.log(chalk.cyan('\n  🤖 AI Diagnosis:\n'));
                console.log(`  ${latest.ai_diagnosis}\n`);
            }
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
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
        const config = getConfig();
        if (!config) return notLoggedIn();

        try {
            const data = await api(config.serverUrl, config.token, '/api/v1/services/');
            const services = data.results || data;
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                console.log(chalk.red(`Service "${serviceName}" not found.`));
                return;
            }

            // Get service detail which includes env_vars
            const detail = await api(config.serverUrl, config.token, `/api/v1/services/${svc.id}/`);
            const envVars = detail.env_vars || [];

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
        }
    });

envCmd
    .command('set <service> <pairs...>')
    .description('Set env vars (KEY=VALUE pairs)')
    .action(async (serviceName, pairs) => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        try {
            const data = await api(config.serverUrl, config.token, '/api/v1/services/');
            const services = data.results || data;
            const svc = services.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
            if (!svc) {
                console.log(chalk.red(`Service "${serviceName}" not found.`));
                return;
            }

            for (const pair of pairs) {
                const [key, ...rest] = pair.split('=');
                const value = rest.join('=');
                if (!key || value === undefined) {
                    console.log(chalk.red(`Invalid format: ${pair}. Use KEY=VALUE`));
                    continue;
                }

                await api(config.serverUrl, config.token,
                    `/api/v1/services/${svc.id}/`, 'PATCH',
                    { env_vars: [{ key, value }] }
                );
                console.log(chalk.green(`  ✓ ${key} set`));
            }
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
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
        const config = getConfig();
        if (!config) return notLoggedIn();

        const ora = (await import('ora')).default;
        const spinner = ora('Scanning GitHub repositories...').start();

        try {
            const { task_id } = await api(config.serverUrl, config.token,
                '/api/v1/cloud/ecosystem/scan/', 'POST');

            spinner.text = 'AI analyzing your ecosystem...';

            // Poll for result
            let result;
            while (true) {
                await new Promise(r => setTimeout(r, 3000));
                const status = await api(config.serverUrl, config.token,
                    `/api/v1/cloud/ecosystem/task_status/?task_id=${task_id}`);

                if (status.status === 'SUCCESS') {
                    result = status.result;
                    break;
                } else if (status.status === 'FAILURE') {
                    spinner.fail(chalk.red(`Scan failed: ${status.error}`));
                    return;
                }
            }

            spinner.succeed(chalk.green('Ecosystem scan complete!'));

            if (result.error) {
                console.log(chalk.red(`\n  Error: ${result.error}`));
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

            // Cache the plan
            setConfig({ ...config, lastPlan: result });
        } catch (err) {
            spinner.fail(chalk.red(`Error: ${err.message}`));
        }
    });

ecoCmd
    .command('deploy')
    .description('Deploy the last scanned ecosystem plan')
    .action(async () => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        const plan = config.lastPlan;
        if (!plan) {
            console.log(chalk.yellow('No cached plan. Run "smsly ecosystem scan" first.'));
            return;
        }

        const ora = (await import('ora')).default;
        const spinner = ora('Deploying ecosystem...').start();

        try {
            const { task_id } = await api(config.serverUrl, config.token,
                '/api/v1/cloud/ecosystem/deploy/', 'POST', { plan });

            // Poll
            let result;
            while (true) {
                await new Promise(r => setTimeout(r, 3000));
                const status = await api(config.serverUrl, config.token,
                    `/api/v1/cloud/ecosystem/task_status/?task_id=${task_id}`);

                if (status.status === 'SUCCESS') {
                    result = status.result;
                    break;
                } else if (status.status === 'FAILURE') {
                    spinner.fail(chalk.red(`Deploy failed: ${status.error}`));
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
        const config = getConfig();
        if (!config) return notLoggedIn();

        try {
            const data = await api(config.serverUrl, config.token, '/api/v1/tokens/');
            const tokens = data.tokens || [];

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
        }
    });

tokensCmd
    .command('create [name]')
    .description('Generate a new API token')
    .action(async (name) => {
        const config = getConfig();
        if (!config) return notLoggedIn();

        try {
            const data = await api(config.serverUrl, config.token,
                '/api/v1/tokens/create/', 'POST', { name: name || 'CLI Token' });

            console.log(chalk.bold('\n  New API token created!\n'));
            console.log(chalk.green(`  ${data.token}\n`));
            console.log(chalk.yellow('  ⚠  Save this token — it won\'t be shown again.\n'));
        } catch (err) {
            console.log(chalk.red(`Error: ${err.message}`));
        }
    });

// ─── HELPERS ────────────────────────────────────────────────────────────────
function notLoggedIn() {
    console.log(chalk.red('\n  Not logged in. Run: smsly login <server-url> --token <api-token>\n'));
}

program.parse();
