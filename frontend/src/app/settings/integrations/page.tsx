import { redirect } from "next/navigation";

export default function IntegrationsPage() {
  redirect("/settings?tab=git");
}
