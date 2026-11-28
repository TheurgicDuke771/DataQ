# Architecture

How DataQ is built, and the record of why. These pages are written for engineers
evaluating, extending or operating the system.

<div class="grid cards" markdown>

-   :material-sitemap:{ .lg .middle } **System architecture**

    ---

    Component, sequence and entity diagrams, kept in sync with the code by a CI drift
    guard.

    [:octicons-arrow-right-24: System architecture](overview.md)

-   :material-file-document-multiple:{ .lg .middle } **Decision records**

    ---

    Every significant design decision as a short ADR with context, options and
    consequences.

    [:octicons-arrow-right-24: ADR index](../adr/README.md)

-   :material-scale-balance:{ .lg .middle } **MCP tool design**

    ---

    Why every AI-facing tool states what it cannot see, and the audit criteria behind it.

    [:octicons-arrow-right-24: Honesty & disclosure](mcp-honesty.md)

-   :material-speedometer:{ .lg .middle } **Performance baseline**

    ---

    Measured throughput and memory per datasource, and the scan caps that protect the
    worker.

    [:octicons-arrow-right-24: Performance baseline](perf-baseline.md)

-   :material-card-text:{ .lg .middle } **Incident evidence card**

    ---

    The payload every incident carries, and what is redacted before it leaves the
    database.

    [:octicons-arrow-right-24: Evidence card](evidence-card.md)

-   :material-source-pull:{ .lg .middle } **Contributing**

    ---

    Working agreements, quality gates and the per-PR workflow.

    [:octicons-arrow-right-24: Contributing](contributing.md)

</div>
