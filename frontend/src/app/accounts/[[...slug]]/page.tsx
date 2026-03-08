import { redirect } from 'next/navigation';
import PlatformNotice from '@/components/public/PlatformNotice';

interface AccountRouteProps {
  params: {
    slug?: string[];
  };
}

export default function AccountsGatewayPage({ params }: AccountRouteProps) {
  const slug = (params.slug || []).map((part) => part.toLowerCase());
  const first = slug[0] || '';
  const second = slug[1] || '';

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
    redirect('/forgot-password');
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

