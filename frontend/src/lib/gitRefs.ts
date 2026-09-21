'use client';

import { githubApi, gitlabApi, bitbucketApi } from '@/lib/api';

export type GitProvider = 'github' | 'gitlab' | 'bitbucket';

export interface GitRefCommit {
  sha: string;
  message: string;
  author?: string;
  date?: string;
}

/** Mirror of the provider detection in BuildTab (keep in sync). */
export function detectGitProvider(repositoryUrl: string): { provider: GitProvider; slug: string } | null {
  if (!repositoryUrl) return null;
  const match =
    repositoryUrl.match(/github\.com\/([^\/]+\/[^\/]+)/) ||
    repositoryUrl.match(/gitlab\.com\/([^\/]+\/[^\/]+)/) ||
    repositoryUrl.match(/bitbucket\.org\/([^\/]+\/[^\/]+)/);
  if (!match) return null;
  let slug = match[1];
  if (slug.endsWith('.git')) slug = slug.slice(0, -4);
  const provider: GitProvider = match[0].includes('github.com')
    ? 'github'
    : match[0].includes('gitlab.com')
      ? 'gitlab'
      : 'bitbucket';
  return { provider, slug };
}

export async function listBranches(repositoryUrl: string): Promise<{ name: string; sha?: string }[]> {
  const detected = detectGitProvider(repositoryUrl);
  if (!detected) return [];
  const { provider, slug } = detected;
  try {
    if (provider === 'github') {
      const data = await githubApi.branches(slug);
      return (Array.isArray(data) ? data : []).map((b: any) => ({
        name: b?.name || String(b),
        sha: b?.commit?.sha,
      }));
    }
    if (provider === 'gitlab') {
      const data = await gitlabApi.branches(slug);
      return (Array.isArray(data) ? data : []).map((b: any) => ({
        name: b?.name || String(b),
        sha: b?.commit?.id,
      }));
    }
    const data = await bitbucketApi.branches(slug);
    return (Array.isArray(data) ? data : []).map((b: any) => ({
      name: b?.name || String(b),
      sha: b?.target?.hash,
    }));
  } catch {
    return [];
  }
}

export async function listCommits(repositoryUrl: string, branch: string): Promise<GitRefCommit[]> {
  const detected = detectGitProvider(repositoryUrl);
  if (!detected || !branch) return [];
  const { provider, slug } = detected;
  try {
    if (provider === 'github') {
      const data = await githubApi.commits(slug, branch);
      return (Array.isArray(data) ? data : []).map((c: any) => ({
        sha: c?.sha || '',
        message: (c?.commit?.message || '').split('\n')[0],
        author: c?.commit?.author?.name,
        date: c?.commit?.author?.date,
      })).filter((c) => c.sha);
    }
    if (provider === 'gitlab') {
      const data = await gitlabApi.commits(slug, branch);
      return (Array.isArray(data) ? data : []).map((c: any) => ({
        sha: c?.id || '',
        message: (c?.title || '').split('\n')[0],
        author: c?.author_name,
        date: c?.authored_date,
      })).filter((c) => c.sha);
    }
    const data = await bitbucketApi.commits(slug, branch);
    return (Array.isArray(data) ? data : []).map((c: any) => ({
      sha: c?.hash || '',
      message: (c?.message || '').split('\n')[0],
      author: typeof c?.author === 'string' ? c.author : c?.author?.raw,
    })).filter((c) => c.sha);
  } catch {
    return [];
  }
}
