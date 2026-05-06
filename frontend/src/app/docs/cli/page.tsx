export default function CLIPage() {
  return (
    <div className="container mx-auto py-12 max-w-3xl prose dark:prose-invert">
      <h1>CLI Reference</h1>
      <p>The Grid CLI allows you to manage services from your terminal.</p>

      <h2>Installation</h2>
      <pre><code>npm install -g cloudneuron-cli</code></pre>

      <h2>Commands</h2>
      <ul>
        <li><code>cn login</code> - Authenticate with your account</li>
        <li><code>cn deploy</code> - Deploy the current directory</li>
        <li><code>cn logs</code> - View runtime logs</li>
        <li><code>cn ssh</code> - SSH into a running container</li>
      </ul>
    </div>
  );
}
