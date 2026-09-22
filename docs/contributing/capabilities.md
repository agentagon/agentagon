# Implementation map

| Behavior | Source | Representative tests |
|---|---|---|
| Shared task admission, questions, cancellation and resumption | [Runtime](../../src/agentagon/workflows/runtime.py), [start contract](../../src/agentagon/workflows/requests.py) | [Lifecycle](../../tests/test_webapp_jobs.py), [real journeys](../../tests/test_workflow_runtime.py) |
| Application agents and goals | [Domain catalog](../../src/agentagon/domain/catalog.py), [discovery](../../src/agentagon/capabilities/discovery.py) | [Identity and scope](../../tests/test_webapp_catalog.py) |
| Independent trace issues and focused repair | [Issues](../../src/agentagon/domain/issues.py), [Discover](../../src/agentagon/workflows/discover/handler.py), [Fix](../../src/agentagon/workflows/fix/handler.py) | [Journeys](../../tests/test_workflow_runtime.py) |
| Managed authors and separate reviewers | [Brain adapters](../../src/agentagon/brain/adapters.py), [review protocol](../../src/agentagon/workflows/procedures.py) | [Backend protocols](../../tests/test_webapp_agents.py), [managed reviews](../../tests/test_webapp_review.py) |
| Trace import, provenance, redaction and bounds | [Trace capabilities](../../src/agentagon/capabilities/traces/) | [Provider adapters](../../tests/test_webapp_providers.py), [trace journeys](../../tests/test_workflow_runtime.py) |
| Evaluation preparation, baseline and optimizer comparisons | [Experiment capabilities](../../src/agentagon/capabilities/experiments/) | [Baselines](../../tests/test_baselines.py), [regression suites](../../tests/test_suites.py) |
| Local group access, revisions and frozen evaluation memory | [Memory](../../src/agentagon/memory/) | [Memory and execution](../../tests/test_workflow_runtime.py) |
| React dashboard and local HTTP controls | [Dashboard](../../src/agentagon/dashboard/), [React source](../../frontend/) | [HTTP](../../tests/test_webapp.py), [browser journeys](../../tests/test_webapp_browser.py) |
| Resumable assessment, improvements and production monitoring | [Assessment/runtime](../../src/agentagon/workflows/production_runtime.py), [scheduler](../../src/agentagon/workflows/scheduler.py), [comparisons](../../src/agentagon/capabilities/production.py), [domain records](../../src/agentagon/domain/monitoring.py) | [Production journeys](../../tests/test_production_runtime.py) |
| Official SDK stdio MCP | [MCP server](../../src/agentagon/mcp/server.py) | [Real stdio client](../../tests/test_workflow_runtime.py) |
| One metadata database and immutable evidence | [Storage](../../src/agentagon/storage/) | [Transactions](../../tests/test_webapp_storage.py), [retention](../../tests/test_storage_retention.py) |

Public interfaces are dashboard and MCP. The CLI only launches application/service/MCP and supplies a hidden private engine surface to managed brain sessions. Old public skills, plugin registration, continuation hooks and the separate dashboard server have been removed.
