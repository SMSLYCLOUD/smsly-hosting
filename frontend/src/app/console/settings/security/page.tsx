import { redirect } from "next/navigation";

export default function MtlsSettingsPage() {
  redirect("/settings?tab=mtls");
}
