import PlatformNotice from '@/components/public/PlatformNotice';

export default function NoticePage() {
  return (
    <PlatformNotice
      badge="Public Notice"
      title="Service notice"
      message="This endpoint is reserved for controlled platform access and cannot be displayed as a generic public page."
      secondaryMessage="If you expected an app here, check DNS records and service routing configuration."
    />
  );
}
