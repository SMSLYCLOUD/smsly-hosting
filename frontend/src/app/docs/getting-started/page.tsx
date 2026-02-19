export default function GettingStartedPage() {
  return (
    <div className="container mx-auto py-12 max-w-3xl prose dark:prose-invert">
      <h1>Getting Started</h1>
      <p>Welcome to CloudNeuron! This guide will help you deploy your first application.</p>

      <h2>Prerequisites</h2>
      <ul>
        <li>A GitHub account</li>
        <li>Code ready to deploy (Node.js, Python, Go, etc.)</li>
      </ul>

      <h2>Step 1: Connect your account</h2>
      <p>Go to Settings &gt; OAuth and connect your GitHub account.</p>

      <h2>Step 2: Create a Service</h2>
      <p>Click &quot;New Service&quot; on the dashboard. Select your repository.</p>

      <h2>Step 3: Deploy</h2>
      <p>Click &quot;Deploy&quot;. CloudNeuron will auto-detect your framework and build your app.</p>
    </div>
  );
}
