import { redirect } from "next/navigation";

export default async function IntegrationsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = searchParams ? await searchParams : undefined;
  const githubApp = params?.github_app;
  const suffix = githubApp ? `&github_app=${encodeURIComponent(String(githubApp))}` : "";
  redirect(`/settings?tab=git${suffix}`);
}
