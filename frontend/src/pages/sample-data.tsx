import { useState } from "react";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Badge from "@/components/ui/Badge/Badge";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import Select from "@/components/ui/Select/Select";
import styles from "./SampleData.module.css";

type Collection = "products" | "users" | "orders";

const products = [
  {
    id: 1,
    name: "Wireless Mouse",
    price: 25.99,
    stock: 50,
  },
  {
    id: 2,
    name: "Mechanical Keyboard",
    price: 79.99,
    stock: 20,
  },
  {
    id: 3,
    name: "USB-C Hub",
    price: 39.99,
    stock: 35,
  },
];

const users = [
  {
    id: 1,
    name: "John Doe",
    email: "john@example.com",
    role: "Admin",
  },
  {
    id: 2,
    name: "Jane Smith",
    email: "jane@example.com",
    role: "User",
  },
];

const orders = [
  {
    id: 101,
    userId: 1,
    total: 105.98,
    status: "Completed",
  },
  {
    id: 102,
    userId: 2,
    total: 39.99,
    status: "Pending",
  },
];

export default function SampleData() {
  const [collection, setCollection] = useState<Collection>("products");

  const recordCount =
    collection === "products"
      ? products.length
      : collection === "users"
        ? users.length
        : orders.length;

  return (
    <DashboardLayout title="Sample Data">
      <div className={styles.content}>
        {/* Page Heading */}
        <div className={styles.pageHeading}>
          <div>
            <h1>Sample Data</h1>
            <p>View and manage the mock records used by your API.</p>
          </div>

          <Button type="button">+ Add Record</Button>
        </div>

        {/* Project Information */}
        <Card>
          <div className={styles.projectInfo}>
            <div>
              <h2>E-commerce API</h2>
              <p>Manage the sample data for your mock API.</p>
            </div>

            <Badge variant="success">Running</Badge>
          </div>
        </Card>

        {/* Collection Selection */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>Data Collections</h2>
              <p>Select a collection to view its records.</p>
            </div>

            <div className={styles.collectionSelect}>
              <Select
                value={collection}
                onChange={(event) =>
                  setCollection(event.target.value as Collection)
                }
              >
                <option value="products">Products</option>
                <option value="users">Users</option>
                <option value="orders">Orders</option>
              </Select>
            </div>
          </div>

          {/* Records Table */}
          <Card>
            <div className={styles.tableHeader}>
              <div>
                <h3>
                  {collection === "products"
                    ? "Products"
                    : collection === "users"
                      ? "Users"
                      : "Orders"}
                </h3>

                <p>{recordCount} records available</p>
              </div>

              <Button type="button">Reset Data</Button>
            </div>

            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  {collection === "products" && (
                    <tr>
                      <th>ID</th>
                      <th>Name</th>
                      <th>Price</th>
                      <th>Stock</th>
                      <th>Actions</th>
                    </tr>
                  )}

                  {collection === "users" && (
                    <tr>
                      <th>ID</th>
                      <th>Name</th>
                      <th>Email</th>
                      <th>Role</th>
                      <th>Actions</th>
                    </tr>
                  )}

                  {collection === "orders" && (
                    <tr>
                      <th>ID</th>
                      <th>User ID</th>
                      <th>Total</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  )}
                </thead>

                <tbody>
                  {collection === "products" &&
                    products.map((product) => (
                      <tr key={product.id}>
                        <td>{product.id}</td>
                        <td>{product.name}</td>
                        <td>${product.price.toFixed(2)}</td>
                        <td>{product.stock}</td>
                        <td>
                          <div className={styles.actions}>
                            <button type="button">Edit</button>
                            <button type="button">Delete</button>
                          </div>
                        </td>
                      </tr>
                    ))}

                  {collection === "users" &&
                    users.map((user) => (
                      <tr key={user.id}>
                        <td>{user.id}</td>
                        <td>{user.name}</td>
                        <td>{user.email}</td>
                        <td>{user.role}</td>
                        <td>
                          <div className={styles.actions}>
                            <button type="button">Edit</button>
                            <button type="button">Delete</button>
                          </div>
                        </td>
                      </tr>
                    ))}

                  {collection === "orders" &&
                    orders.map((order) => (
                      <tr key={order.id}>
                        <td>{order.id}</td>
                        <td>{order.userId}</td>
                        <td>${order.total.toFixed(2)}</td>
                        <td>
                          <Badge
                            variant={
                              order.status === "Completed"
                                ? "success"
                                : "warning"
                            }
                          >
                            {order.status}
                          </Badge>
                        </td>
                        <td>
                          <div className={styles.actions}>
                            <button type="button">Edit</button>
                            <button type="button">Delete</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>
    </DashboardLayout>
  );
}
