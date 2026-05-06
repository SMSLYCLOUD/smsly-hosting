export default function TermsPage() {
  return (
    <div className="container mx-auto py-12 max-w-3xl prose dark:prose-invert">
      <h1>Terms of Service</h1>
      <p>Last updated: {new Date().toLocaleDateString()}</p>

      <h2>1. Acceptance of Terms</h2>
      <p>By accessing and using Grid, you accept and agree to be bound by the terms and provision of this agreement.</p>

      <h2>2. Use License</h2>
      <p>Permission is granted to temporarily download one copy of the materials (information or software) on Grid&apos;s website for personal, non-commercial transitory viewing only.</p>

      <h2>3. Disclaimer</h2>
      <p>The materials on Grid&apos;s website are provided on an &apos;as is&apos; basis. Grid makes no warranties, expressed or implied, and hereby disclaims and negates all other warranties including, without limitation, implied warranties or conditions of merchantability, fitness for a particular purpose, or non-infringement of intellectual property or other violation of rights.</p>

      <h2>4. Limitations</h2>
      <p>In no event shall Grid or its suppliers be liable for any damages (including, without limitation, damages for loss of data or profit, or due to business interruption) arising out of the use or inability to use the materials on Grid&apos;s website.</p>
    </div>
  );
}
