
import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { LineChart, Line, XAxis, YAxis, Tooltip } from 'recharts';

export default function Dashboard() {
  const [count, setCount] = useState(0);
  const data = [
    {name: 'A', uv: 400},
    {name: 'B', uv: 300},
    {name: 'C', uv: 200}
  ];

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold">Analytics Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardContent>
            <h2 className="text-xl">User Counter</h2>
            <p className="text-4xl font-bold">{count}</p>
            <Button onClick={() => setCount(count + 1)}>Increment</Button>
          </CardContent>
        </Card>
        <Card>
          <CardContent>
            <LineChart width={300} height={200} data={data}>
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="uv" stroke="#8884d8" />
            </LineChart>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
