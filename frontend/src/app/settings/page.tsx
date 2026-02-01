"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Settings as SettingsIcon, User, Bell, Shield, Cloud } from "lucide-react";
import { motion } from "framer-motion";
import { useToast } from "@/components/ui/use-toast";

export default function SettingsPage() {
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    await new Promise(r => setTimeout(r, 1000));
    setSaving(false);
    toast({ title: "Settings saved", description: "Your preferences have been updated." });
  };

  return (
    <div className="container mx-auto py-10 max-w-4xl">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h1 className="text-3xl font-bold flex items-center gap-3">
          <SettingsIcon className="h-8 w-8 text-primary" />
          Settings
        </h1>
        <p className="text-muted-foreground">Manage your account and preferences.</p>
      </motion.div>

      <Tabs defaultValue="profile" className="space-y-6">
        <TabsList className="grid grid-cols-4 w-full">
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <User className="h-4 w-4" /> Profile
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" /> Notifications
          </TabsTrigger>
          <TabsTrigger value="security" className="flex items-center gap-2">
            <Shield className="h-4 w-4" /> Security
          </TabsTrigger>
          <TabsTrigger value="providers" className="flex items-center gap-2">
            <Cloud className="h-4 w-4" /> Providers
          </TabsTrigger>
        </TabsList>

        <TabsContent value="profile">
          <Card>
            <CardHeader>
              <CardTitle>Profile Settings</CardTitle>
              <CardDescription>Update your personal information.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>First Name</Label>
                  <Input placeholder="John" />
                </div>
                <div className="space-y-2">
                  <Label>Last Name</Label>
                  <Input placeholder="Doe" />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Email</Label>
                <Input type="email" placeholder="john@example.com" />
              </div>
              <Button onClick={handleSave} disabled={saving}>
                {saving ? "Saving..." : "Save Changes"}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="notifications">
          <Card>
            <CardHeader>
              <CardTitle>Notification Preferences</CardTitle>
              <CardDescription>Choose how you want to be notified.</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground">Notification settings coming soon.</p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="security">
          <Card>
            <CardHeader>
              <CardTitle>Security Settings</CardTitle>
              <CardDescription>Manage your account security.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Current Password</Label>
                <Input type="password" />
              </div>
              <div className="space-y-2">
                <Label>New Password</Label>
                <Input type="password" />
              </div>
              <Button variant="outline">Change Password</Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers">
          <Card>
            <CardHeader>
              <CardTitle>Cloud Providers</CardTitle>
              <CardDescription>Connect your cloud infrastructure.</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground">Provider configuration coming soon.</p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
