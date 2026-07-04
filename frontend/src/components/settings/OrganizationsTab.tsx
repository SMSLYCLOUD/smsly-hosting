"use client";

import React, { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Users, Building, Plus, Mail, Shield } from "lucide-react";
import { organizationsApi } from "@/lib/api";

export function OrganizationsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [orgs, setOrgs] = useState<any[]>([]);
  const [ssoConfigs, setSsoConfigs] = useState<any[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string>("");
  const [newOrgName, setNewOrgName] = useState("");
  const [newIdpUrl, setNewIdpUrl] = useState("");
  const [newIdpEntityId, setNewIdpEntityId] = useState("");

  const fetchData = async () => {
    try {
      const data = await organizationsApi.list();
      setOrgs(data);
      if (data.length > 0 && !activeOrgId) {
        setActiveOrgId(data[0].id);
      }
      
      const ssoData = await organizationsApi.getSSO();
      setSsoConfigs(ssoData);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCreateOrg = async () => {
    if (!newOrgName.trim()) return;
    try {
      const org = await organizationsApi.create(newOrgName);
      setOrgs([...orgs, org]);
      setActiveOrgId(org.id);
      setNewOrgName("");
      toast({ title: "Organization created" });
    } catch (err: any) {
      toast({ title: "Error creating organization", description: err.message, variant: "destructive" });
    }
  };

  const handleCreateSSO = async () => {
    if (!activeOrgId || !newIdpUrl.trim()) return;
    try {
      const sso = await organizationsApi.createSSO({
        organization: activeOrgId,
        idp_sso_url: newIdpUrl,
        idp_entity_id: newIdpEntityId,
        is_active: true
      });
      setSsoConfigs([...ssoConfigs, sso]);
      setNewIdpUrl("");
      setNewIdpEntityId("");
      toast({ title: "SSO Configured" });
    } catch (err: any) {
      toast({ title: "Error configuring SSO", description: err.message, variant: "destructive" });
    }
  };

  if (loading) return <div className="flex justify-center p-8"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;

  const activeOrg = orgs.find(o => o.id === activeOrgId);
  const activeSso = ssoConfigs.find(s => s.organization === activeOrgId);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Organizations</CardTitle>
          <CardDescription>Manage your top-level tenant organizations.</CardDescription>
        </CardHeader>
        <CardContent>
          {orgs.length > 0 ? (
            <div className="flex flex-wrap gap-2 mb-6">
              {orgs.map((org) => (
                <Button
                  key={org.id}
                  variant={activeOrgId === org.id ? "default" : "outline"}
                  onClick={() => setActiveOrgId(org.id)}
                  className="flex items-center gap-2"
                >
                  <Building className="h-4 w-4" />
                  {org.name}
                </Button>
              ))}
            </div>
          ) : (
            <div className="text-center p-6 text-muted-foreground bg-muted/20 rounded-md mb-6">
              You don&apos;t belong to any organizations yet.
            </div>
          )}

          <div className="flex gap-2 items-end max-w-sm">
            <div className="space-y-2 flex-1">
              <Label>New Organization Name</Label>
              <Input
                value={newOrgName}
                onChange={(e) => setNewOrgName(e.target.value)}
                placeholder="Acme Corp"
              />
            </div>
            <Button onClick={handleCreateOrg} disabled={!newOrgName.trim()}>
              <Plus className="h-4 w-4 mr-2" />
              Create
            </Button>
          </div>
        </CardContent>
      </Card>

      {activeOrg && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Single Sign-On (SSO)
            </CardTitle>
            <CardDescription>Configure SAML/OIDC for {activeOrg.name}</CardDescription>
          </CardHeader>
          <CardContent>
            {activeSso ? (
              <div className="space-y-4">
                <div className="p-4 bg-muted/20 rounded-md">
                  <p className="font-medium text-sm">SSO is active</p>
                  <p className="text-xs text-muted-foreground mt-1">IDP URL: {activeSso.idp_sso_url}</p>
                </div>
                <Button variant="destructive" onClick={async () => {
                  try {
                    await organizationsApi.deleteSSO(activeSso.id);
                    setSsoConfigs(ssoConfigs.filter(s => s.id !== activeSso.id));
                    toast({ title: "SSO configuration removed" });
                  } catch (e: any) {
                    toast({ title: "Error", description: e.message, variant: "destructive" });
                  }
                }}>
                  Remove SSO Configuration
                </Button>
              </div>
            ) : (
              <div className="space-y-4 max-w-md">
                <div className="space-y-2">
                  <Label>Identity Provider SSO URL</Label>
                  <Input 
                    value={newIdpUrl}
                    onChange={(e) => setNewIdpUrl(e.target.value)}
                    placeholder="https://dev-xxxx.okta.com/app/xxx/sso/saml"
                  />
                </div>
                <div className="space-y-2">
                  <Label>IDP Entity ID</Label>
                  <Input 
                    value={newIdpEntityId}
                    onChange={(e) => setNewIdpEntityId(e.target.value)}
                    placeholder="http://www.okta.com/exk..."
                  />
                </div>
                <Button onClick={handleCreateSSO} disabled={!newIdpUrl.trim()}>
                  Enable SSO
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
