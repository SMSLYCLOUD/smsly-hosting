"use client"

import * as React from "react"
import { Check, ChevronsUpDown, PlusCircle, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { teamsApi, Team } from "@/lib/api"

type PopoverTriggerProps = React.ComponentPropsWithoutRef<typeof PopoverTrigger>

interface TeamSwitcherProps extends PopoverTriggerProps {
    className?: string
}

export default function TeamSwitcher({ className }: TeamSwitcherProps) {
  const [open, setOpen] = React.useState(false)
  const [showNewTeamDialog, setShowNewTeamDialog] = React.useState(false)
  const [teams, setTeams] = React.useState<Team[]>([])
  const [selectedTeam, setSelectedTeam] = React.useState<Team | null>(null)
  const [newTeamName, setNewTeamName] = React.useState("")
  const [loading, setLoading] = React.useState(false)

  React.useEffect(() => {
    const fetchTeams = async () => {
      try {
        const data = await teamsApi.list()
        setTeams(data)
        const storedId = localStorage.getItem("smsly_active_team")

        if (storedId) {
          const found = data.find((t) => t.id === storedId)
          if (found) {
            setSelectedTeam(found)
            return
          }
        }

        // Default to first team
        if (data.length > 0) {
          setSelectedTeam(data[0])
          localStorage.setItem("smsly_active_team", data[0].id)
        }
      } catch (error) {
        console.error("Failed to fetch teams", error)
      }
    }
    fetchTeams()
  }, [])

  const handleTeamSelect = (team: Team) => {
    setSelectedTeam(team)
    setOpen(false)
    localStorage.setItem("smsly_active_team", team.id)
    window.dispatchEvent(new CustomEvent("smsly:team-changed", { detail: team.id }))
  }

  const handleCreateTeam = async () => {
    if (!newTeamName.trim()) return
    setLoading(true)
    try {
      const newTeam = await teamsApi.create(newTeamName)
      setTeams([...teams, newTeam])
      setSelectedTeam(newTeam)
      localStorage.setItem("smsly_active_team", newTeam.id)
      window.dispatchEvent(new CustomEvent("smsly:team-changed", { detail: newTeam.id }))
      setShowNewTeamDialog(false)
      setNewTeamName("")
    } catch (error) {
      console.error("Failed to create team", error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={showNewTeamDialog} onOpenChange={setShowNewTeamDialog}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="Select a team"
            className={cn("w-[200px] justify-between", className)}
          >
            <Users className="mr-2 h-4 w-4" />
            {selectedTeam ? selectedTeam.name : "Select Team"}
            <ChevronsUpDown className="ml-auto h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[200px] p-0">
          <Command>
            <CommandList>
              <CommandInput placeholder="Search team..." />
              <CommandEmpty>No team found.</CommandEmpty>
              <CommandGroup heading="Teams">
                {teams.map((team) => (
                  <CommandItem
                    key={team.id}
                    onSelect={() => handleTeamSelect(team)}
                    className="text-sm"
                  >
                    <Users className="mr-2 h-4 w-4" />
                    {team.name}
                    <Check
                      className={cn(
                        "ml-auto h-4 w-4",
                        selectedTeam?.id === team.id
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
            <CommandSeparator />
            <CommandList>
              <CommandGroup>
                <DialogTrigger asChild>
                  <CommandItem
                    onSelect={() => {
                      setOpen(false)
                      setShowNewTeamDialog(true)
                    }}
                  >
                    <PlusCircle className="mr-2 h-5 w-5" />
                    Create Team
                  </CommandItem>
                </DialogTrigger>
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Team</DialogTitle>
          <DialogDescription>
            Add a new team to manage services and members.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2 pb-4">
          <div className="space-y-2">
            <Label htmlFor="name">Team Name</Label>
            <Input
              id="name"
              placeholder="Acme Inc."
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setShowNewTeamDialog(false)}
          >
            Cancel
          </Button>
          <Button onClick={handleCreateTeam} disabled={loading}>
            {loading ? "Creating..." : "Create Team"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
