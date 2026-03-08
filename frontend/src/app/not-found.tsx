import PlatformNotice from '@/components/public/PlatformNotice';

export default function NotFound() {
  return (
    <PlatformNotice
      badge="404 Notice"
      title="This page is not publicly available"
      message="The link is invalid, expired, or not exposed for public access."
      secondaryMessage="If this is your platform, verify routing and custom domain mapping in Settings and Service Domains."
    />
  );
}
