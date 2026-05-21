// @ts-nocheck
import React, { useState, useEffect } from 'react';
import { NavLink } from 'react-router-dom';
import { Input, Collapse, List, Badge } from '@mantine/core';
import { IconSearch, IconFolder, IconGitBranch } from '@tabler/icons-react';

/**
 * Sidebar that groups repositories by logical project.
 * It fetches a lightweight summary endpoint that returns:
 *   [{ project: string, repos: [{ name: string, id: string, status: string }] }]
 * The component provides:
 *   • fuzzy search
 *   • collapsible project sections
 *   • status badge per repo
 */
export const ProjectSidebar: React.FC = () => {
  const [data, setData] = useState<Array<any>>([]);
  const [search, setSearch] = useState('');
  const [openProjects, setOpenProjects] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetch('/api/v1/ecosystem/sidebar/')
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData([]));
  }, []);

  const toggleProject = (name: string) => {
    setOpenProjects((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const filtered = data.filter((proj) =>
    proj.project.toLowerCase().includes(search.toLowerCase()) ||
    proj.repos.some((r: any) => r.name.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div style={{ width: 260, height: '100vh', overflowY: 'auto', padding: 12, background: '#f8f9fa' }}>
      <Input
        placeholder="Search projects…"
        icon={<IconSearch size={14} />}
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        mb="sm"
      />
      {filtered.map((proj) => (
        <div key={proj.project} style={{ marginBottom: 8 }}>
          <List.Item
            icon={<IconFolder size={16} />}
            onClick={() => toggleProject(proj.project)}
            style={{ cursor: 'pointer', fontWeight: 500 }}
          >
            {proj.project}
          </List.Item>
          <Collapse in={openProjects[proj.project] ?? true}>
            <List spacing="xs" mt={4} ml={12}>
              {proj.repos.map((repo: any) => (
                <NavLink
                  key={repo.id}
                  to={`/project/${proj.project}/repo/${repo.id}`}
                  style={{ textDecoration: 'none' }}
                >
                  <List.Item icon={<IconGitBranch size={14} />}> 
                    {repo.name}
                    {repo.status && (
                      <Badge color={repo.status === 'active' ? 'green' : 'gray'} size="xs" ml={4}>
                        {repo.status}
                      </Badge>
                    )}
                  </List.Item>
                </NavLink>
              ))}
            </List>
          </Collapse>
        </div>
      ))}
    </div>
  );
};
