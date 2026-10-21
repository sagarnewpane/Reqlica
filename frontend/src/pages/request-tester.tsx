import { useState } from "react";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import Input from "@/components/ui/Input/Input";
import Select from "@/components/ui/Select/Select";
import styles from "./RequestTester.module.css";

type RequestTab = "Params" | "Headers" | "Body";

const requestHistory = [
  {
    method: "GET",
    endpoint: "/products",
    status: 200,
    time: "120 ms",
  },
  {
    method: "POST",
    endpoint: "/products",
    status: 201,
    time: "185 ms",
  },
  {
    method: "GET",
    endpoint: "/products/1",
    status: 200,
    time: "95 ms",
  },
];

const sampleResponse = {
  products: [
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
  ],
};

export default function RequestTester() {
  const [method, setMethod] = useState("GET");
  const [endpoint, setEndpoint] = useState("/products");
  const [activeTab, setActiveTab] = useState<RequestTab>("Params");
  const [requestBody, setRequestBody] = useState(
    '{\n  "name": "Wireless Mouse",\n  "price": 25.99\n}',
  );

  return (
    <DashboardLayout title="Request Tester">
      <div className={styles.content}>
        {/* Page Heading */}
        <div className={styles.pageHeading}>
          <h1>Request Tester</h1>
          <p>Configure API requests and preview mock responses.</p>
        </div>

        {/* Request Configuration */}
        <div className={styles.section}>
          <Card>
            <div className={styles.requestPanel}>
              <h2>Send Request</h2>

              <div className={styles.requestBar}>
                <div className={styles.methodSelect}>
                  <Select
                    value={method}
                    onChange={(event) => setMethod(event.target.value)}
                  >
                    <option value="GET">GET</option>
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="PATCH">PATCH</option>
                    <option value="DELETE">DELETE</option>
                  </Select>
                </div>

                <div className={styles.endpointInput}>
                  <Input
                    value={endpoint}
                    onChange={(event) => setEndpoint(event.target.value)}
                    placeholder="/products"
                  />
                </div>

                <Button type="button">Send Request</Button>
              </div>

              {/* Request Tabs */}
              <div className={styles.tabs}>
                {(["Params", "Headers", "Body"] as RequestTab[]).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    className={
                      activeTab === tab ? styles.activeTab : styles.tab
                    }
                    onClick={() => setActiveTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              {/* Tab Content */}
              <div className={styles.tabContent}>
                {activeTab === "Params" && (
                  <div className={styles.placeholder}>
                    <h3>Query Parameters</h3>
                    <p>Configure query parameters for your request.</p>

                    <div className={styles.fieldRow}>
                      <Input placeholder="Key" />
                      <Input placeholder="Value" />
                    </div>
                  </div>
                )}

                {activeTab === "Headers" && (
                  <div className={styles.placeholder}>
                    <h3>Request Headers</h3>
                    <p>Configure HTTP headers for your request.</p>

                    <div className={styles.fieldRow}>
                      <Input placeholder="Header name" />
                      <Input placeholder="Header value" />
                    </div>
                  </div>
                )}

                {activeTab === "Body" && (
                  <div className={styles.placeholder}>
                    <h3>Request Body</h3>
                    <p>Enter the JSON body for POST, PUT, or PATCH requests.</p>

                    <textarea
                      className={styles.bodyTextarea}
                      value={requestBody}
                      onChange={(event) => setRequestBody(event.target.value)}
                      rows={8}
                      spellCheck={false}
                    />
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>

        {/* Response Viewer */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <h2>Response</h2>
            <p>Sample response preview for your mock API.</p>
          </div>

          <Card>
            <div className={styles.responsePanel}>
              <div className={styles.responseInfo}>
                <span className={styles.status}>200 OK</span>
                <span>120 ms</span>
                <span>application/json</span>
              </div>

              <pre className={styles.codeBlock}>
                {JSON.stringify(sampleResponse, null, 2)}
              </pre>
            </div>
          </Card>
        </div>

        {/* Request History */}
        <div className={styles.section}>
          <div className={styles.sectionHeading}>
            <h2>Request History</h2>
            <p>Previously tested API requests (sample data).</p>
          </div>

          <Card>
            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Endpoint</th>
                    <th>Status</th>
                    <th>Time</th>
                  </tr>
                </thead>

                <tbody>
                  {requestHistory.map((request, index) => (
                    <tr key={index}>
                      <td>
                        <span className={styles.method}>{request.method}</span>
                      </td>

                      <td>{request.endpoint}</td>
                      <td>{request.status}</td>
                      <td>{request.time}</td>
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
