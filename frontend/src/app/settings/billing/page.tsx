"use client"

import * as React from "react"
import { CreditCard, Receipt, BarChart3, Check, Loader2, ArrowUpRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useToast } from "@/components/ui/use-toast"
import { cn } from "@/lib/utils"

const PLANS = [
    {
        name: "Hobby",
        price: "$0",
        description: "For personal projects",
        features: ["512MB RAM / Service", "Shared CPU", "Community Support"],
        current: true
    },
    {
        name: "Pro",
        price: "$29",
        description: "For serious applications",
        features: ["8GB RAM / Service", "Dedicated CPU", "Priority Support", "SLA 99.9%"],
        current: false
    },
    {
        name: "Enterprise",
        price: "Custom",
        description: "For large scale deployments",
        features: ["Unlimted RAM", "Custom Hardware", "24/7 Phone Support", "SLA 99.99%"],
        current: false
    }
]

export default function BillingPage() {
    const { toast } = useToast()
    const [isLoading, setIsLoading] = React.useState(false)

    const handleUpgrade = (plan: string) => {
        setIsLoading(true)
        // Simulate Stripe Checkout redirect
        setTimeout(() => {
            setIsLoading(false)
            toast({ title: "Redirecting to Stripe", description: `Upgrading to ${plan} plan...` })
        }, 1500)
    }

    return (
        <div className="container py-8 max-w-5xl space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Billing & Usage</h1>
                <p className="text-muted-foreground">Manage your subscription and view resource consumption.</p>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                <Card className="bio-card">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Create Balance</CardTitle>
                        <CreditCard className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">$0.00</div>
                        <p className="text-xs text-muted-foreground">Available credits</p>
                        <Button size="sm" className="mt-4 w-full" variant="outline">
                            Add Funds
                        </Button>
                    </CardContent>
                </Card>
                <Card className="bio-card">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Estimated Cost</CardTitle>
                        <BarChart3 className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">$12.45</div>
                        <p className="text-xs text-muted-foreground">+20.1% from last month</p>
                        <Progress value={33} className="mt-4" />
                        <p className="text-xs text-muted-foreground mt-2">Next invoice: Mar 1, 2026</p>
                    </CardContent>
                </Card>
                 <Card className="bio-card">
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Active Services</CardTitle>
                        <ArrowUpRight className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">3</div>
                        <p className="text-xs text-muted-foreground">Running instances</p>
                    </CardContent>
                </Card>
            </div>

            <Tabs defaultValue="plans" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="plans">Plans</TabsTrigger>
                    <TabsTrigger value="invoices">Invoices</TabsTrigger>
                </TabsList>
                
                <TabsContent value="plans" className="space-y-4">
                    <div className="grid gap-6 lg:grid-cols-3">
                        {PLANS.map((plan) => (
                            <Card key={plan.name} className={cn("flex flex-col", plan.current ? "border-primary" : "")}>
                                <CardHeader>
                                    <div className="flex justify-between items-start">
                                        <div>
                                            <CardTitle>{plan.name}</CardTitle>
                                            <CardDescription>{plan.description}</CardDescription>
                                        </div>
                                        {plan.current && <Badge variant="secondary">Current</Badge>}
                                    </div>
                                    <div className="mt-4">
                                        <span className="text-3xl font-bold">{plan.price}</span>
                                        <span className="text-muted-foreground"> / month</span>
                                    </div>
                                </CardHeader>
                                <CardContent className="flex-1">
                                    <ul className="space-y-2 text-sm">
                                        {plan.features.map((feature) => (
                                            <li key={feature} className="flex items-center">
                                                <Check className="mr-2 h-4 w-4 text-primary" />
                                                {feature}
                                            </li>
                                        ))}
                                    </ul>
                                </CardContent>
                                <CardFooter>
                                    <Button 
                                        className="w-full" 
                                        variant={plan.current ? "outline" : "default"}
                                        disabled={plan.current || isLoading}
                                        onClick={() => handleUpgrade(plan.name)}
                                    >
                                        {isLoading && !plan.current ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                        {plan.current ? "Current Plan" : "Upgrade"}
                                    </Button>
                                </CardFooter>
                            </Card>
                        ))}
                    </div>
                </TabsContent>
                
                <TabsContent value="invoices">
                    <Card>
                        <CardHeader>
                            <CardTitle>Invoice History</CardTitle>
                            <CardDescription>View and download past invoices.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                {[1, 2, 3].map((i) => (
                                    <div key={i} className="flex items-center justify-between border-b pb-4 last:border-0 last:pb-0">
                                        <div className="flex items-center gap-4">
                                            <div className="h-9 w-9 rounded bg-muted flex items-center justify-center">
                                                <Receipt className="h-4 w-4" />
                                            </div>
                                            <div>
                                                <div className="font-medium">March {i}, 2026</div>
                                                <div className="text-sm text-muted-foreground">Pro Plan - $29.00</div>
                                            </div>
                                        </div>
                                        <Button variant="ghost" size="sm">Download PDF</Button>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    )
}
