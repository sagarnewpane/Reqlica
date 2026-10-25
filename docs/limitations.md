# Limitations

## Runtime

- Supports local use on macOS and Linux with Python 3.12+. Windows is untested.
- Generated APIs use Python and FastAPI only.
- APIs run in local workers; checks use subprocesses. There is no execution
  sandbox. Generated code has your filesystem and network permissions.
- Management APIs and MCP are unauthenticated. Keep them on loopback.
- Python packages share one environment across projects.
- There is no hosted SaaS or multi-user workspace.

## AI

- Models must support tool calls. Output quality and provider compatibility vary.
- Tool schemas and project/conversation context are relatively large. Further
  optimization could reduce request size, token usage, latency, and cost.
- Generated code and tests need review. Passing checks cover only tested behavior.
- Chat advances through browser requests; closing or pausing it stops future
  steps after the current one. It is not an unattended job runner.
- Selected context goes to the chosen provider. Saved conversations can contain
  user-entered sensitive information.

See [Project AI](guides/generation.md) for budgets and recovery.

## Data and operations

- Data batches are limited to 100 records / 128 KiB and up to 10 related tables.
  Unsupported formats, including password hashes and custom validators, are refused.
- Preview validation is a snapshot. Live insertion rechecks state and is not
  idempotent; inspect data before retrying after a lost response.
- Draft requests discard their changes. Use one behavior test for stateful flows.
- Database setup only adds tables. Existing tables need reviewed migrations;
  rollback can discard later writes. Code restoration does not rewind data.
- Activation briefly interrupts project traffic. External database writers are
  outside Reqlica's worker coordination.
- Logs and redaction are best effort. Backups and history have no automatic pruning.
- ZIP archives are limited to 32 MiB compressed, 128 MiB unpacked, and 2,000 files.
  They do not include installed packages or external services.
- There is no generic live-record editor or dedicated CI scenario runner.

[Future work](roadmap.md) · [Documentation](README.md)
