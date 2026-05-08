import React, { useEffect, useState } from 'react';
import { servicesApi } from '@/lib/api';
import { Loader2 } from 'lucide-react';
import { ForceGraph2D } from 'react-force-graph';

/**
 * Simple dependency graph visualiser.
 * It fetches the ``/services/<id>/dependencies/`` endpoint and renders a
 * force‑directed graph where the current service is the centre node.
 */
export const DependencyGraph: React.FC<{ serviceId: string }> = ({ serviceId }) => {
  const [graph, setGraph] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    servicesApi
      .retrieveDependencies(serviceId)
      .then((data) => {
        // Transform API payload into nodes/links for react-force-graph
        const nodes = [{ id: data.service.id, name: data.service.name, main: true }];
        const links: any[] = [];
        data.depends_on.forEach((d: any) => {
          nodes.push({ id: d.id, name: d.name });
          links.push({ source: data.service.id, target: d.id });
        });
        data.dependents.forEach((d: any) => {
          nodes.push({ id: d.id, name: d.name });
          links.push({ source: d.id, target: data.service.id });
        });
        setGraph({ nodes, links });
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [serviceId]);

  if (loading) {
    return <Loader2 className="animate-spin w-6 h-6 text-muted-foreground" />;
  }
  if (!graph) {
    return <p className="text-sm text-muted-foreground">No dependency data.</p>;
  }

  return (
    <ForceGraph2D
      graphData={graph}
      nodeAutoColorBy="group"
      nodeCanvasObject={(node, ctx, globalScale) => {
        const label = (node as any).name;
        const fontSize = 12 / globalScale;
        ctx.font = `${fontSize}px Sans-Serif`;
        ctx.fillStyle = (node as any).main ? '#ffcc00' : '#ffffff';
        ctx.textAlign = 'center';
        ctx.fillText(label, node.x ?? 0, node.y ?? 0);
      }}
      linkDirectionalArrowLength={3.5}
      linkDirectionalArrowRelPos={1}
      width={window.innerWidth * 0.8}
      height={350}
    />
  );
};
