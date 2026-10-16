import DashboardLayout from "@/components/layout/DashboardLayout";
import Badge from "@/components/ui/Badge/Badge";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import styles from "./DraftReview.module.css";

const endpoints = [
  {
    method: "GET",
    path: "/products",
    description: "Get all products",
  },
  {
    method: "GET",
    path: "/products/{id}",
    description: "Get a product by ID",
  },
  {
    method: "POST",
    path: "/products",
    description: "Create a new product",
  },
  {
    method: "PUT",
    path: "/products/{id}",
    description: "Update an existing product",
  },
  {
    method: "DELETE",
    path: "/products/{id}",
    description: "Delete a product",
  },
];

const dataModels = [
  {
    name: "Product",
    fields: "id, name, description, price, stock",
  },
  {
    name: "User",
    fields: "id, name, email, password",
  },
  {
    name: "Order",
    fields: "id, userId, products, total, status",
  },
];

export default function DraftReview() {
  return (
    <DashboardLayout title="Draft Review">
      <div className={styles.content}>
        {/* Page Heading */}
        <div className={styles.pageHeading}>
          <div>
            <h1>Draft Review</h1>
            <p>Review your generated API structure before approving it.</p>
          </div>

          <Badge variant="warning">Pending Review</Badge>
        </div>

        {/* Draft Information */}
        <Card>
          <div className={styles.draftInfo}>
            <div>
              <h2>E-commerce API</h2>
              <p>A mock REST API for managing products, users, and orders.</p>
            </div>

            <div className={styles.draftStats}>
              <span>5 Endpoints</span>
              <span>3 Data Models</span>
              <span>Draft v1</span>
            </div>
          </div>
        </Card>

        {/* Endpoints Section */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <h2>Proposed Endpoints</h2>
            <p>Review the endpoints included in your API draft.</p>
          </div>

          <Card>
            <div className={styles.endpointList}>
              {endpoints.map((endpoint) => (
                <div
                  key={`${endpoint.method}-${endpoint.path}`}
                  className={styles.endpointRow}
                >
                  <span className={styles.method}>{endpoint.method}</span>
                  <span className={styles.path}>{endpoint.path}</span>
                  <span className={styles.description}>
                    {endpoint.description}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Data Models Section */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <h2>Data Models</h2>
            <p>Review the data structures used by your API.</p>
          </div>

          <div className={styles.modelGrid}>
            {dataModels.map((model) => (
              <Card key={model.name}>
                <div className={styles.modelCard}>
                  <h3>{model.name}</h3>
                  <p>{model.fields}</p>
                </div>
              </Card>
            ))}
          </div>
        </div>

        {/* Review Actions */}
        <div className={styles.actions}>
          <p>Review the API structure carefully before approval.</p>

          <div className={styles.actionButtons}>
            <Button type="button">Request Changes</Button>
            <Button type="button">Approve Draft</Button>
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
