# Future improvements

The current release focuses on a complete local workflow: author an API, check
it, review and publish it, then test clients against persistent data. See
[feature status](guides/feature-status.md) for shipped behavior and
[limitations](limitations.md) for current constraints.

The following are possible improvements, not implemented features or delivery
commitments.

| Area                 | Improvement                                                                                                                             | Reason                                                                                                                                       |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| AI reliability       | Broader provider/model regression coverage and more actionable recovery diagnostics.                                                    | Reduce failures caused by varying tool support, malformed output, and quota behavior.                                                        |
| Context optimization | Reduce AI request size with shorter tool schemas, more focused project/source selection, and better conversation compaction.            | Lower token usage, latency, and provider cost while preserving the information needed for correct generation and tool use.                   |
| Contract coverage    | Support more imported OpenAPI constructs and strengthen behavior checks derived from requirements.                                      | Reduce gaps between a specification and the verified implementation.                                                                         |
| Dataset preparation  | Broader supported application formats and explicit user-supplied reference selection.                                                   | Make linked datasets easier to prepare without guessing existing IDs or credentials.                                                         |
| Dependency isolation | Per-project environments and portable dependency manifests.                                                                             | Avoid one project's package requirements affecting another.                                                                                  |
| Storage maintenance  | Retention controls and clearer backup/disk-usage management.                                                                            | Make long-lived local workspaces easier to maintain.                                                                                         |
| Observability        | Optional live log subscriptions and richer bounded diagnostics.                                                                         | Shorten the feedback loop while keeping capture deliberate.                                                                                  |
| Automation           | A dedicated CI runner and reproducible scenario execution against real backends.                                                        | Extend local manual testing into automated integration workflows.                                                                            |
| Platform support     | Validate and document a Windows setup.                                                                                                  | Expand beyond the current macOS/Linux support boundary.                                                                                      |
| Team use             | Management authentication, authorization, and remote workspace design.                                                                  | Establish identity and access control before supporting shared deployments.                                                                  |
| Sandboxed execution  | Integrate an execution sandbox for generated APIs and draft checks, with filesystem, network, and resource controls.                    | Current local workers do not restrict generated Python's OS/network access.                                                                  |
| Hosted SaaS          | Explore a hosted API-building experience similar to Lovable, with accounts, isolated workspaces, managed execution, and deployment.     | Let users create and run APIs without installing Reqlica locally; requires tenant isolation, authentication, quotas, and hosted persistence. |
| Framework support    | Experiment with backend frameworks beyond FastAPI through framework-specific generation, worker, validation, and API-contract adapters. | Support different stacks while preserving the draft, check, review, and publication workflow.                                                |

New capabilities should preserve revision-pinned checks, explicit publication,
reviewed live-data changes, and recovery after uncertain responses. External MCP
clients and built-in Project AI have different permissions; extending one should
not silently expand the other.

[Documentation index](README.md) · [Project overview](../README.md)
