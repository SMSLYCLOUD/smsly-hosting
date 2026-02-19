export default function AddonsPage() {
  return (
    <div className="container mx-auto py-12 max-w-3xl prose dark:prose-invert">
      <h1>Addons</h1>
      <p>CloudNeuron supports managed addons for databases, caching, and storage.</p>

      <h2>Supported Addons</h2>
      <ul>
        <li><strong>PostgreSQL:</strong> Relational database</li>
        <li><strong>Redis:</strong> In-memory cache</li>
        <li><strong>MongoDB:</strong> NoSQL database</li>
        <li><strong>Qdrant:</strong> Vector database for AI</li>
      </ul>

      <h2>Provisioning</h2>
      <p>Addons can be provisioned via the &quot;Addons&quot; tab in your service dashboard or via the API.</p>
    </div>
  );
}
