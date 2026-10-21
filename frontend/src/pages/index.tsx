import DashboardLayout from "@/components/layout/DashboardLayout";
import Badge from "@/components/ui/Badge/Badge";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import Input from "@/components/ui/Input/Input";
import styles from "./Home.module.css";

const projects = [
  {
    name: "E-commerce API",
    description: "Mock API for an e-commerce application.",
    status: "Running",
    variant: "success" as const,
    endpoints: 12,
  },
  {
    name: "Authentication API",
    description: "Mock API for user authentication.",
    status: "Draft",
    variant: "warning" as const,
    endpoints: 6,
  },
  {
    name: "Payment API",
    description: "Mock API for payment testing.",
    status: "Stopped",
    variant: "danger" as const,
    endpoints: 8,
  },
];

export default function Home() {
  return (
    <DashboardLayout title="Projects">
      <section className={styles.content}>
        <div className={styles.pageHeading}>
          <div>
            <h1>Your API workspace</h1>
            <p>Create, manage, and test your mock API projects.</p>
          </div>

          <Button>+ New Project</Button>
        </div>

        <div className={styles.search}>
          <Input placeholder="Search projects..." />
        </div>

        <div className={styles.projectGrid}>
          {projects.map((project) => (
            <Card key={project.name}>
              <div className={styles.projectCard}>
                <div className={styles.projectHeading}>
                  <h2>{project.name}</h2>
                  <Badge variant={project.variant}>{project.status}</Badge>
                </div>

                <p>{project.description}</p>

                <div className={styles.projectFooter}>
                  <span>{project.endpoints} endpoints</span>
                  <span>View project →</span>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </section>
    </DashboardLayout>
  );
}
