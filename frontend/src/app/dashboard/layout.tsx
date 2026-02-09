import { Sidebar } from "@/components/sidebar";
import { UserNav } from "@/components/user-nav";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="h-full relative">
      <div className="hidden h-full md:flex md:w-72 md:flex-col md:fixed md:inset-y-0 z-[80] bg-gray-900">
        <Sidebar />
      </div>
      <main className="md:pl-72">
        <div className="flex items-center p-4 border-b h-16 bg-white dark:bg-slate-950">
           <div className="ml-auto flex items-center space-x-4">
              <UserNav />
           </div>
        </div>
        <div className="p-8 bg-slate-50 dark:bg-slate-900 min-h-[calc(100vh-4rem)]">
          {children}
        </div>
      </main>
    </div>
  );
}
