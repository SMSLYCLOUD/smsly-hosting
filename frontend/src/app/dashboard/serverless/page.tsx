import { useState } from 'react';
import { api } from '@/lib/api';

export default function ServerlessPage() {
  const [functionName, setFunctionName] = useState('');
  const [runtime, setRuntime] = useState('python3.9');
  const [code, setCode] = useState('');
  const [status, setStatus] = useState('');

  const handleDeploy = async () => {
    setStatus('Deploying...');
    try {
      // In a real app, this would upload a zip or file
      // Here we assume the API handles raw code for simplicity or uses the upload endpoint
      const response = await api.post('/deployments/upload/', {
        name: functionName,
        runtime: runtime,
        code: code, // This would likely be a file upload in practice
        type: 'function'
      });
      setStatus(`Deployed: ${response.data.deployment_id}`);
    } catch (error) {
      setStatus('Failed to deploy');
      console.error(error);
    }
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">New Serverless Function</h1>

      <div className="space-y-4 max-w-xl">
        <div>
          <label className="block text-sm font-medium">Function Name</label>
          <input
            type="text"
            className="w-full border p-2 rounded"
            value={functionName}
            onChange={(e) => setFunctionName(e.target.value)}
          />
        </div>

        <div>
          <label className="block text-sm font-medium">Runtime</label>
          <select
            className="w-full border p-2 rounded"
            value={runtime}
            onChange={(e) => setRuntime(e.target.value)}
          >
            <option value="python3.9">Python 3.9</option>
            <option value="nodejs18">Node.js 18</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium">Handler Code</label>
          <textarea
            className="w-full border p-2 rounded h-40 font-mono"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="def handler(event): return 'Hello World'"
          />
        </div>

        <button
          onClick={handleDeploy}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Deploy Function
        </button>

        {status && <p className="mt-2 text-sm text-gray-600">{status}</p>}
      </div>
    </div>
  );
}
