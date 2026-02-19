export default function PrivacyPage() {
  return (
    <div className="container mx-auto py-12 max-w-3xl prose dark:prose-invert">
      <h1>Privacy Policy</h1>
      <p>Last updated: {new Date().toLocaleDateString()}</p>

      <h2>1. Information We Collect</h2>
      <p>We collect information you provide directly to us, such as when you create an account, update your profile, or request customer support.</p>

      <h2>2. How We Use Information</h2>
      <p>We use the information we collect to operate, maintain, and provide the features and functionality of the Service.</p>

      <h2>3. Data Retention</h2>
      <p>We retain your personal information for as long as necessary to provide the Service and fulfill the transactions you have requested, or for other essential purposes such as complying with our legal obligations.</p>
    </div>
  );
}
