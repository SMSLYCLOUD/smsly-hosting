'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import axios from 'axios';

interface Template {
  id: string;
  name: string;
  description: string;
  icon_url: string;
  repository_url: string;
  default_port: number;
}

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const router = useRouter();

  useEffect(() => {
    // Direct fetch for MVP, move to api client ideally
    axios.get(process.env.NEXT_PUBLIC_API_URL + '/templates/').then(res => setTemplates(res.data));
  }, []);

  const handleUse = (t: Template) => {
    const query = new URLSearchParams({
        repo: t.repository_url,
        port: t.default_port.toString(),
        name: t.name.toLowerCase().replace(/ /g, '-')
    }).toString();
    router.push(`/new?${query}`);
  };

  return (
    <main className="flex min-h-screen flex-col items-center p-24">
      <h1 className="text-4xl font-bold mb-8">Template Marketplace</h1>
      <p className="text-gray-500 mb-12">One-click deploy production-ready applications.</p>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-full max-w-6xl">
        {templates.map(t => (
            <div key={t.id} className="bg-white dark:bg-zinc-800 rounded-xl shadow p-6 hover:ring-2 ring-blue-500 transition-all cursor-pointer" onClick={() => handleUse(t)}>
                <div className="flex items-center gap-4 mb-4">
                    {t.icon_url && <img src={t.icon_url} className="w-12 h-12" alt={t.name} />}
                    <h3 className="font-bold text-xl">{t.name}</h3>
                </div>
                <p className="text-gray-500 mb-6">{t.description}</p>
                <button className="w-full bg-gray-100 dark:bg-zinc-700 text-blue-600 font-bold py-2 rounded hover:bg-gray-200 dark:hover:bg-zinc-600">
                    Use Template →
                </button>
            </div>
        ))}
      </div>
    </main>
  );
}
