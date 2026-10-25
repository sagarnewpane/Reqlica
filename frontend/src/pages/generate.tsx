import { useEffect, useState } from 'react';
import Link from 'next/link';
import DashboardLayout from '@/components/layout/DashboardLayout';
import Card from '@/components/ui/Card/Card';
import { projectRequest, type Project } from '@/lib/projects';
import styles from './Home.module.css';

export default function GenerateAPI() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    projectRequest()
      .then((data) => {
        if (active) setProjects(data.projects);
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <DashboardLayout title="Project chat">
      <section className={styles.content}>
        <div className={styles.pageHeading}>
          <div>
            <h1>Build with Reqlica</h1>
            <p>
              Choose a project to ask questions, refine an API, or prepare
              changes in a draft.
            </p>
          </div>
        </div>
        {loading && <p role="status">Loading projects…</p>}
        {error && (
          <p role="alert">
            {error} <Link href="/">Return to projects</Link>
          </p>
        )}
        {!loading && !error && !projects.length && (
          <p>
            Create your first project to start chatting.{' '}
            <Link href="/">Go to projects →</Link>
          </p>
        )}
        <div className={styles.projectGrid}>
          {projects.map((project) => (
            <Card key={project.id}>
              <div className={styles.projectCard}>
                <h2>
                  <Link
                    className={styles.projectLink}
                    href={`/projects/${project.id}`}
                  >
                    {project.name || 'Unavailable project'} →
                  </Link>
                </h2>
                <p className={styles.directory}>{project.directory}</p>
                <span>{project.endpoints.length} endpoints</span>
              </div>
            </Card>
          ))}
        </div>
      </section>
    </DashboardLayout>
  );
}
