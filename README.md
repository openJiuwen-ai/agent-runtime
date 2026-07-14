# openJiuwen Agent Runtime

openJiuwen Agent Runtime (Runtime) is a runtime and deployment system for AI agents. Its goal is to move agents reliably from **development** to **production**.

## Positioning

Runtime focuses on:

- Deploying different kinds of agents in a uniform way and exposing them as services;
- Isolating runtime instances for users and spaces in multi-tenant setups;
- Supporting multiple deployment modes (process, Docker, Kubernetes);
- Managing the lifecycle of running agents (deploy, inspect, delete, health checks).

## Core capabilities

### 1) Unified deployment management

Runtime exposes standard REST APIs. You deploy agents using configuration files and metadata, with support for deploy, list, delete, and state management.

### 2) Multiple deployment strategies

Runtime uses a strategy-based deployment model:

- **`subprocess`**: run each agent in an isolated subprocess (default);
- **`docker`**: run agents in containers;
- **`k8s`**: run agents on Kubernetes.

### 3) Multi-tenant isolation

Runtime can inject tenant context (e.g. `user_id`, `space_id`) to isolate resources and operations per tenant.

### 4) Extensible architecture

Runtime is modular: deployment strategies, agent types, and foundational services can be extended independently for different scenarios.

## Architecture

Runtime is built from these main parts:

- **Agent Runtime (overall)**: full lifecycle from publish and deploy to external calls.
- **Service (`service/`)**: `AgentApp` / `BaseApp` wrap FastAPI and conversation APIs (`/query`, `/health`, `/reset_conversation`).
- **Management (`management/`)**: `DeploymentManager` and deployers (process, Docker, K8s) handle strategy selection, instance lifecycle, and persistence.
- **Server (`server/`)**: management REST (deploy, list, delete), tenant middleware, health; conversation runs inside the agent process (Service + Applications).
- **Foundation (`foundation/`)**: SQLite / MySQL, optional Redis, ports, deploy directories, virtual environments, Docker helpers, logging, etc.
- **Applications (`applications/`)**: concrete agent implementations and runtime support (e.g. low-code agent, workflow IR execution), based on Service, started as separate processes by Management.

## Typical deployment flow

1. The client calls Runtime’s deploy API with agent configuration;
2. Runtime selects a deployment strategy and creates a deployment record;
3. The strategy executor starts the instance (process / container / K8s);
4. The agent service starts and exposes standard endpoints (e.g. `/query`, `/health`);
5. Callers use Runtime or the agent’s address for traffic and operations.

## Repository layout

```text
agent-runtime/
├── applications/      # Agent apps and runtime support
├── cli/               # CLI tooling
├── docker/            # Docker build files
├── foundation/        # DB, ports, deploy helpers, etc.
├── management/        # Deployment core and deployers
├── server/            # FastAPI management service
├── service/           # App abstractions and service layer
├── scripts/           # Start and build scripts
└── docs/              # Documentation (zh / en)
```

## Prerequisites

- Python >= 3.11.4
- `uv` >= 0.25.x
- `git` (clone the repo and submodules used by scripts)
- `bash` (Linux / macOS) or **PowerShell** (Windows)

## Quick start

### 1) Clone the repository

```bash
git clone https://gitcode.com/openJiuwen/agent-runtime.git
cd agent-runtime
```

### 2) Configure environment

```bash
cd server
cp .env.example .env
```

Edit `server/.env` as needed. Important fields include:

- **`RUNTIME_DB_TYPE`**: `sqlite`, `mysql`, `gaussdb`, or `opengauss`
- **`IP`**: reachable address for Runtime / agents
- **`LOWCODE_IMAGE`**: low-code agent container image (required when using Docker/K8s-style flows)

Notes:

- The default install only includes dependencies for `sqlite` and `mysql`.
- When `RUNTIME_DB_TYPE=gaussdb` or `RUNTIME_DB_TYPE=opengauss`, Runtime additionally needs `async-gaussdb`.
- If you use the repository startup scripts, they detect `RUNTIME_DB_TYPE` from `server/.env` and install `foundation[gaussdb]` automatically. For a manual setup, run `uv pip install -e "./foundation[gaussdb]"`.

For a full reference, see **`docs/en/2. Configuration.md`** (Chinese: `docs/zh/2. 配置说明.md`).

### 3) Start Runtime (recommended)

Return to the **repository root**, then run:

**Linux / macOS**

```bash
cd ..
bash scripts/run-server.sh
```

**Windows (PowerShell)**

```powershell
cd ..
.\scripts\run-server.ps1
```

The script installs dependencies, builds wheels, and starts the service. Default listen port is **`8186`** (override with **`PORT`** in `.env`).

## API overview

Management service (typical defaults):

- `GET /health` — service health
- `POST /api/v1/agents/deploy` — deploy an agent
- `GET /api/v1/agents` — list deployments
- `GET /api/v1/agents/{deployment_id}` — deployment details
- `DELETE /api/v1/agents/{deployment_id}` — remove a deployment

After deployment, the agent app on its own port usually exposes:

- `GET /health` — app health
- `POST /query` — chat / query (streaming supported)
- `POST /reset_conversation` — reset session context

## Working with openJiuwen Studio

Configure Runtime host and port in **agent-studio**’s backend environment, then you can:

- Publish agents to Runtime in one step
- Try conversations after publish
- Generate API call examples (curl / Python / JavaScript)
- Unpublish and reclaim runtime instances

## Configuration topics

- Listen port and service address
- Database type (SQLite / MySQL / GaussDB / openGauss)
- Deployment mode (process; container and K8s evolving)
- Tenant context propagation and isolation

Details: **`docs/en/2. Configuration.md`**.

## Documentation

**English**

- Overview: `docs/en/0. Project Overview.md`
- Quick start: `docs/en/1. Quick Start.md`
- Configuration: `docs/en/2. Configuration.md`
- Agent deployment: `docs/en/3. Agent Deployment.md`
- Agent integration: `docs/en/4. Agent Integration.md`

**中文**

- `docs/zh/0. 项目介绍.md`
- `docs/zh/1. 快速开始.md`
- `docs/zh/2. 配置说明.md`
- `docs/zh/3. Agent部署.md`
- `docs/zh/4. Agent接入.md`

## Roadmap

- Strengthen Docker / Kubernetes deployment paths
- CLI integration
- Web UI integration
