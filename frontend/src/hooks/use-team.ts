import { useState, useEffect } from 'react';
import { teamsApi, Team } from '@/lib/api';

export function useTeam() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [activeTeamId, setActiveTeamId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshTeams = async () => {
    try {
      const data = await teamsApi.list();
      setTeams(data);

      // Auto-select first team if none selected and teams exist
      const storedId = localStorage.getItem('smsly_active_team');
      if (storedId && data.find(t => t.id === storedId)) {
        setActiveTeamId(storedId);
      } else if (data.length > 0) {
        // If no team selected, or selected team not in list, select the first one
        setActiveTeamId(data[0].id);
        localStorage.setItem('smsly_active_team', data[0].id);
        window.dispatchEvent(new CustomEvent("smsly:team-changed", { detail: data[0].id }));
      } else {
        setActiveTeamId(null);
        localStorage.removeItem('smsly_active_team');
      }
    } catch (e) {
      console.error("Failed to load teams", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshTeams();

    // Listen for cross-component updates
    const handleChange = (e: CustomEvent) => {
        setActiveTeamId(e.detail);
    };

    if (typeof window !== 'undefined') {
        window.addEventListener('smsly:team-changed', handleChange as EventListener);
    }

    return () => {
        if (typeof window !== 'undefined') {
            window.removeEventListener('smsly:team-changed', handleChange as EventListener);
        }
    };
  }, []);

  const selectTeam = (teamId: string) => {
    setActiveTeamId(teamId);
    localStorage.setItem('smsly_active_team', teamId);
    window.dispatchEvent(new CustomEvent("smsly:team-changed", { detail: teamId }));
  };

  const activeTeam = teams.find(t => t.id === activeTeamId) || null;

  return {
    teams,
    activeTeam,
    activeTeamId,
    loading,
    refreshTeams,
    selectTeam
  };
}
