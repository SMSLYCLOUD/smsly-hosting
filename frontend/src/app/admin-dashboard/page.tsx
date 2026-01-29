'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';

export default function AdminDashboardPage() {
  const [stats, setStats] = useState({
    total_services: 0,
    total_deployments: 0,
    active_instances: 0,
    revenue_estimate: 0
  });

  useEffect(() => {
    // Simulate fetching aggregated stats
    setTimeout(() => {
        setStats({
            total_services: 142,
            total_deployments: 893,
            active_instances: 256,
            revenue_estimate: 12500
        });
    }, 500);
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center p-24 bg-gray-50 dark:bg-black">
      <h1 className="text-4xl font-bold mb-8">Operator Command Center</h1>

      <div className="grid grid-cols-4 gap-6 w-full max-w-6xl mb-12">
        <div className="bg-white dark:bg-zinc-800 p-6 rounded-xl shadow border-l-4 border-blue-500">
            <h3 className="text-gray-500 text-sm font-bold uppercase">Total Services</h3>
            <p className="text-4xl font-bold mt-2">{stats.total_services}</p>
        </div>
        <div className="bg-white dark:bg-zinc-800 p-6 rounded-xl shadow border-l-4 border-green-500">
            <h3 className="text-gray-500 text-sm font-bold uppercase">Active Instances</h3>
            <p className="text-4xl font-bold mt-2">{stats.active_instances}</p>
        </div>
        <div className="bg-white dark:bg-zinc-800 p-6 rounded-xl shadow border-l-4 border-purple-500">
            <h3 className="text-gray-500 text-sm font-bold uppercase">Total Deployments</h3>
            <p className="text-4xl font-bold mt-2">{stats.total_deployments}</p>
        </div>
        <div className="bg-white dark:bg-zinc-800 p-6 rounded-xl shadow border-l-4 border-yellow-500">
            <h3 className="text-gray-500 text-sm font-bold uppercase">Est. Monthly Revenue</h3>
            <p className="text-4xl font-bold mt-2">${stats.revenue_estimate}</p>
        </div>
      </div>

      <div className="w-full max-w-6xl bg-white dark:bg-zinc-800 rounded-xl shadow p-8">
        <h3 className="text-xl font-bold mb-6">Recent Platform Events</h3>
        <table className="w-full text-left">
            <thead>
                <tr className="border-b dark:border-zinc-700">
                    <th className="pb-4">Event</th>
                    <th className="pb-4">User</th>
                    <th className="pb-4">Service</th>
                    <th className="pb-4">Time</th>
                </tr>
            </thead>
            <tbody>
                <tr className="border-b border-gray-100 dark:border-zinc-700">
                    <td className="py-4 text-green-600">Deployment Success</td>
                    <td>user_123</td>
                    <td>django-api-prod</td>
                    <td>2 mins ago</td>
                </tr>
                <tr className="border-b border-gray-100 dark:border-zinc-700">
                    <td className="py-4 text-red-600">Build Failed</td>
                    <td>user_456</td>
                    <td>react-frontend</td>
                    <td>15 mins ago</td>
                </tr>
                <tr>
                    <td className="py-4 text-blue-600">Add-on Provisioned</td>
                    <td>user_789</td>
                    <td>redis-cache-01</td>
                    <td>1 hour ago</td>
                </tr>
            </tbody>
        </table>
      </div>
    </main>
  );
}
