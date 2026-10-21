import DashboardLayout from "@/components/layout/DashboardLayout";
import Badge from "@/components/ui/Badge/Badge";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import Input from "@/components/ui/Input/Input";
import styles from "./Endpoints.module.css";

const endpoints = [
  {
    method: "GET",
    path: "/products",
    description: "Retrieve all products",
    status: "Active",
  },
  {
    method: "GET",
    path: "/products/{id}",
    description: "Retrieve a product by ID",
    status: "Active",
  },
  {
    method: "POST",
    path: "/products",
    description: "Create a new product",
    status: "Active",
  },
  {
    method: "PUT",
    path: "/products/{id}",
    description: "Update an existing product",
    status: "Active",
  },
  {
    method: "DELETE",
    path: "/products/{id}",
    description: "Delete a product",
    status: "Active",
  },
];

export default function Endpoints() {
  return (
    <DashboardLayout title="Endpoints">
      <div className={styles.content}>
        {/* Page Heading */}
        <div className={styles.pageHeading}>
          <div>
            <h1>API Endpoints</h1>
            <p>View and manage the endpoints available in your mock API.</p>
          </div>

          <Button type="button">+ Add Endpoint</Button>
        </div>

        {/* Project Information */}
        <Card>
          <div className={styles.projectInfo}>
            <div>
              <h2>E-commerce API</h2>
              <p>Mock REST API for products, users, and orders.</p>
            </div>

            <div className={styles.projectStats}>
              <Badge variant="success">Running</Badge>
              <span>5 Endpoints</span>
            </div>
          </div>
        </Card>

        {/* Endpoint List */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>Endpoint List</h2>
              <p>Browse the available API routes.</p>
            </div>

            <div className={styles.search}>
              <Input placeholder="Search endpoints..." />
            </div>
          </div>

          <Card>
            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Endpoint</th>
                    <th>Description</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>

                <tbody>
                  {endpoints.map((endpoint) => (
                    <tr key={`${endpoint.method}-${endpoint.path}`}>
                      <td>
                        <span className={styles.method}>{endpoint.method}</span>
                      </td>

                      <td className={styles.path}>{endpoint.path}</td>

                      <td>{endpoint.description}</td>

                      <td>
                        <Badge variant="success">{endpoint.status}</Badge>
                      </td>

                      <td>
                        <button type="button" className={styles.viewButton}>
                          View Details
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        {/* Endpoint Details Preview */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <h2>Endpoint Details</h2>
            <p>Preview an endpoint&apos;s response structure.</p>
          </div>

          <Card>
            <div className={styles.details}>
              <div className={styles.detailsHeading}>
                <span className={styles.method}>GET</span>
                <h3>/products</h3>
              </div>

              <p>Retrieve all products from the mock API.</p>

              <h4>Sample Response</h4>

              <pre className={styles.codeBlock}>
                {JSON.stringify(
                  {
                    products: [
                      {
                        id: 1,
                        name: "Wireless Mouse",
                        price: 25.99,
                        stock: 50,
                      },
                    ],
                  },
                  null,
                  2,
                )}
              </pre>
            </div>
          </Card>
        </div>
      </div>
    </DashboardLayout>
  );
}
