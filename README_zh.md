# openJiuwen Agent Runtime

openJiuwen Agent Runtime（简称 Runtime）是面向 AI Agent 的运行时与部署管理系统，核心目标是将智能体从“开发态”稳定地带到“生产态”。

## 项目定位

Runtime 主要解决以下问题：

- 如何把不同类型的 Agent 统一部署并对外服务；
- 如何在多租户场景下隔离不同用户和空间的运行实例；
- 如何支持多种部署方式（进程、Docker、Kubernetes）；
- 如何对运行态 Agent 做生命周期管理（部署、查看、删除、健康检查）。

## 核心能力

### 1）统一的部署管理

Runtime 暴露标准 REST API，可通过配置文件和元数据完成 Agent 部署，支持部署、查询、删除与状态管理。

### 2）多部署策略支持

Runtime 内置策略化部署能力，支持三种模式：

- `subprocess`：以独立子进程方式运行 Agent（默认）；
- `docker`：以容器方式运行 Agent；
- `k8s`：在 Kubernetes 集群中运行 Agent。

### 3）多租户隔离

Runtime 支持租户上下文注入（如 `user_id`、`space_id`），用于隔离不同租户的 Agent 资源和操作范围。

### 4）可扩展架构

Runtime 采用模块化设计，部署策略、Agent 类型、基础能力均可独立扩展，便于在不同业务场景中快速接入新能力。

## 系统架构

Runtime 由以下核心模块组成：

- **Agent Runtime（总体）**：覆盖从发布、部署到对外调用的全生命周期。
- **Service（引擎模块，`service/`）**：`AgentApp` / `BaseApp` 封装 FastAPI 与对话面 API（`/query`、`/health`、`/reset_conversation`），承载业务请求。
- **Management（管理模块，`management/`）**：`DeploymentManager` 与 Deployer（进程、Docker、K8s 等）负责部署策略、实例启停与状态持久化。
- **Server（运行时服务模块，`server/`）**：暴露管理面 REST（部署与实例查询、删除等）、租户中间件与健康检查；对话在已拉起的 Agent 进程（Service + Applications）内完成。
- **Foundation（基础服务模块，`foundation/`）**：SQLite / MySQL、可选 Redis、端口与部署目录、虚拟环境、Docker 工具、日志等共用基础能力。
- **Applications（应用适配模块，`applications/`）**：具体智能体实现与运行支撑（如低码 Agent、工作流 IR 执行），基于 Service，由 Management 拉起为独立进程。

## 典型部署流程

1. 客户端调用 Runtime 部署接口，提交 Agent 配置；
2. Runtime 根据配置选择部署策略并创建部署记录；
3. 策略执行器完成实例拉起（进程 / 容器 / K8s）；
4. Agent 服务启动并暴露标准接口（如 `/query`、`/health`）；
5. 业务侧通过 Runtime 或 Agent 地址进行调用与运维管理。

## 目录结构

```text
agent-runtime/
├── applications/      # Agent 应用与运行支持
├── cli/               # CLI 命令行工具
├── docker/            # Docker 构建文件
├── foundation/        # 基础库：DB、端口、部署工具等
├── management/        # 部署管理核心与部署器
├── server/            # FastAPI 管理服务
├── service/           # 应用抽象与服务层
├── scripts/           # 启动与构建脚本
└── docs/              # 项目文档
```

## 环境要求

- python>=3.11.4
- uv>=0.25.x
- git（用于拉取代码）
- bash（Linux/macOS 系统）或 PowerShell（Windows 系统）

## 快速开始

### 1) 获取代码

```bash
git clone https://gitcode.com/openJiuwen/agent-runtime.git
cd agent-runtime
```

### 2) 准备环境配置

```bash
cd server
cp .env.example .env
```

然后按需修改 `server/.env`，重点包括：

- `RUNTIME_DB_TYPE`：支持 `sqlite` / `mysql` / `gaussdb` / `opengauss`
- `IP`：Runtime 服务地址
- `LOWCODE_IMAGE`：低码 Agent 相关镜像配置

说明：

- 默认安装仅包含 `sqlite` / `mysql` 所需依赖。
- 当 `RUNTIME_DB_TYPE=gaussdb` 或 `RUNTIME_DB_TYPE=opengauss` 时，需要额外安装 `async-gaussdb`。
- 使用仓库自带启动脚本时，脚本会根据 `server/.env` 中的 `RUNTIME_DB_TYPE` 自动安装 `foundation[gaussdb]` 可选依赖；手工安装时可执行 `uv pip install -e "./foundation[gaussdb]"`。

完整配置项说明请参考 `docs/zh/2. 配置说明.md`。

### 3) 一键启动 Runtime 服务

Linux / macOS:

```bash
bash scripts/run-server.sh
```

Windows（PowerShell）:

```powershell
.\scripts\run-server.ps1
```

脚本会自动完成依赖安装、构建与服务拉起。默认监听端口为 `8186`（可通过 `PORT` 覆盖）。

## API 概览

运行时管理服务默认提供以下管理接口：

- `GET /health`：服务健康检查
- `POST /api/v1/agents/deploy`：部署 Agent
- `GET /api/v1/agents`：查询部署列表
- `GET /api/v1/agents/{deployment_id}`：查询部署详情
- `DELETE /api/v1/agents/{deployment_id}`：删除部署

部署完成后，Agent 应用本身可通过其运行端口提供：

- `GET /health`：应用健康检查
- `POST /query`：对话查询（支持流式响应）
- `POST /reset_conversation`：重置会话上下文

## 与 openJiuwen Studio 协作

在 `agent-studio` 中配置 Runtime 地址后，可直接完成：

- 智能体一键发布到 Runtime
- 发布后在线对话验证
- 自动生成 API 调用示例（curl / Python / JavaScript）
- 一键下架并回收运行态实例

## 配置说明

当前支持的关键配置能力：

- 运行端口与服务地址配置
- 数据库类型配置（SQLite / MySQL / GaussDB / openGauss）
- 部署模式选择（进程；容器与 K8s 持续完善）
- 租户上下文透传与隔离策略

详细配置请参考 `docs/zh/2. 配置说明.md`。

## 文档导航

- 项目介绍：`docs/zh/0. 项目介绍.md`
- 快速开始：`docs/zh/1. 快速开始.md`
- 配置说明：`docs/zh/2. 配置说明.md`
- Agent 部署：`docs/zh/3. Agent部署.md`
- Agent 接入：`docs/zh/4. Agent接入.md`

## Roadmap

- 完善 Docker / Kubernetes 部署能力
- 补齐 CLI 接入能力
- 补齐 WebUI 接入能力
