# Future work

These are possible next steps, not implemented features.

- **Sandboxed execution:** run generated APIs and checks in an execution sandbox
  with filesystem, network, and resource limits instead of relying on local workers.
- **Hosted SaaS:** explore a hosted building experience similar to Lovable.
  This needs accounts, tenant isolation, managed execution, quotas, and persistence.
- **More frameworks:** experiment with backends beyond FastAPI, including adapters
  for generation, workers, checks, and API contracts.
- **Smaller AI requests:** shorten tool schemas, select source more carefully,
  and improve conversation compaction to reduce tokens, latency, and cost.
- **Better verification:** expand model/provider tests and imported OpenAPI support.
- **Project isolation:** separate Python environments and portable dependency lists.
- **Maintenance:** add retention controls, disk-usage reporting, and log subscriptions.
- **Automation and platforms:** add CI scenarios and validate Windows support.

Keep source checks, explicit publication, and reviewed data changes in each workflow.

[Limitations](limitations.md) · [Documentation](README.md)
