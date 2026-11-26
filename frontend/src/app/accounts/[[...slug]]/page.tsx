import { redirect } from 'next/navigation';
import PlatformNotice from '@/components/public/PlatformNotice';

interface AccountRouteProps {
  params: Promise<{
    slug?: string[];
  }>;
}

export default async function AccountsGatewayPage(props: AccountRouteProps) {
  const params = await props.params;
  const rawSlug = params.slug || [];
  const slug = rawSlug.map((part) => part.toLowerCase());
  const first = slug[0] || '';
  const second = slug[1] || '';
  const third = slug[2] || '';

  if (!first || first === 'login') {
    redirect('/login');
  }

  if (first === 'signup') {
    redirect('/register');
  }

  if (first === 'logout') {
    redirect('/logout');
  }

  if (first === 'password' && second === 'reset') {
    if (third === 'key' && rawSlug[3]) {
      const combined = rawSlug[3];
      const dashIdx = combined.indexOf('-');
      if (dashIdx > 0) {
        const uid = combined.slice(0, dashIdx);
        const token = combined.slice(dashIdx + 1);
        redirect(`/reset-password?uid=${encodeURIComponent(uid)}&token=${encodeURIComponent(token)}`);
      }
      redirect(`/reset-password?token=${encodeURIComponent(combined)}`);
    }
    redirect('/forgot-password');
  }

  if (first === 'confirm-email') {
    const key = rawSlug[1];
    if (key) {
      redirect(`/verify-email?key=${encodeURIComponent(key)}`);
    }
    redirect('/verify-email');
  }

  return (
    <PlatformNotice
      badge="Account Notice"
      title="Account route moved to frontend"
      message="This account endpoint is not rendered as a backend system page anymore."
      secondaryMessage="Use the frontend auth pages for login, registration, password reset, and logout."
    />
  );
}
