import { useState } from "react";
import DashboardLayout from "@/components/layout/DashboardLayout";
import Button from "@/components/ui/Button/Button";
import Card from "@/components/ui/Card/Card";
import styles from "./Generate.module.css";

type InputMode = "prompt" | "markdown" | "openapi";

export default function GenerateAPI() {
  const [inputMode, setInputMode] = useState<InputMode>("prompt");
  const [description, setDescription] = useState("");

  return (
    <DashboardLayout title="Generate API">
      <div className={styles.content}>
        {/* Page Heading */}
        <div className={styles.pageHeading}>
          <h1>Generate API</h1>
          <p>
            Describe your API requirements or import an existing specification
            to create a mock API draft.
          </p>
        </div>

        {/* Generate API Form */}
        <Card>
          <div className={styles.form}>
            <div className={styles.sectionHeading}>
              <h2>Describe your API</h2>
              <p>Choose how you want to provide your requirements.</p>
            </div>

            {/* Input Tabs */}
            <div className={styles.tabs}>
              <button
                type="button"
                className={
                  inputMode === "prompt" ? styles.activeTab : styles.tab
                }
                onClick={() => {
                  setInputMode("prompt");
                  setDescription("");
                }}
              >
                Prompt
              </button>

              <button
                type="button"
                className={
                  inputMode === "markdown" ? styles.activeTab : styles.tab
                }
                onClick={() => {
                  setInputMode("markdown");
                  setDescription("");
                }}
              >
                Markdown
              </button>

              <button
                type="button"
                className={
                  inputMode === "openapi" ? styles.activeTab : styles.tab
                }
                onClick={() => {
                  setInputMode("openapi");
                  setDescription("");
                }}
              >
                OpenAPI
              </button>
            </div>

            {/* API Requirements Input */}
            <div className={styles.inputSection}>
              <label htmlFor="api-description">
                {inputMode === "prompt"
                  ? "API Description"
                  : inputMode === "markdown"
                    ? "Markdown Requirements"
                    : "OpenAPI Specification"}
              </label>

              <textarea
                id="api-description"
                className={styles.textarea}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={
                  inputMode === "prompt"
                    ? "Example: Create a REST API for an online store with products, users, and orders..."
                    : inputMode === "markdown"
                      ? "# API Requirements\n\n## Users\n- Create user\n- Get user by ID"
                      : "openapi: 3.0.0\ninfo:\n  title: Example API\n  version: 1.0.0"
                }
                rows={12}
              />
            </div>

            {/* Form Footer */}
            <div className={styles.formFooter}>
              <p>
                Your API draft will be available for review before approval.
              </p>

              <Button type="button">Generate Draft</Button>
            </div>
          </div>
        </Card>

        {/* How It Works */}
        <div className={styles.infoSection}>
          <h2>How it works</h2>

          <div className={styles.steps}>
            <Card>
              <h3>01. Describe</h3>
              <p>Enter your API requirements using a prompt or document.</p>
            </Card>

            <Card>
              <h3>02. Review</h3>
              <p>Review the proposed endpoints and data structures.</p>
            </Card>

            <Card>
              <h3>03. Run</h3>
              <p>Approve the draft and use your mock API for testing.</p>
            </Card>
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
