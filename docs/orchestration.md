# Managed execution and resumption

One local service owns tasks started from dashboard or MCP. The runtime starts a managed author session and separate reviewer sessions, freezes task settings and limits, and retains exact execution and review identities. Interface disconnection leaves work running. Service interruption requires explicit resume; pending execution is reconciled rather than duplicated.

Use Settings to choose a coding backend/model and bounded execution profile. Application-agent discovery remains separate. Optimize can allocate independent candidate/reflection work within the accepted host and runner capacity. Focused Fix uses direct bounded retries. The same execution machinery records failed trials and regression results.

Questions, cancellation and resumption use the shared task controls. There are no host lifecycle hooks or an external calling-agent supervisor. See [workflow behavior](workflows.md) and [MCP](mcp.md).
