# JiuwenSwarm Enterprise Edition Product Overview

JiuwenSwarm is a multi-agent collaboration system from the openJiuwen community. It helps users complete complex tasks through task decomposition, specialized roles, and dynamic collaboration. **JiuwenSwarm Enterprise Edition builds on the collaboration capabilities of the Personal Edition to support shared Agent services across departments and users, with resource elasticity, multi-tenant isolation, centralized management, and security auditing.** Organizations can deploy it on their own server clusters, provide Agent capabilities as a shared service, and centrally manage runtime resources and access permissions.

In the Enterprise Edition, JiuwenSwarm executes multi-agent tasks, Agent-Runtime manages sessions and runtime resources, and Manager centrally maintains identities, capabilities, and cluster configurations. Employees access authorized Agents through a browser or other supported channels. Administrators use the management console to define who can use the service, which capabilities they can access, and which resources run their tasks.

## Core Capabilities

### Resource Elasticity

Request volumes and task durations vary with business demand. Agent-Runtime manages AgentServer resource pools according to configured policies. It prioritizes existing session bindings and available capacity, adds runtime instances when capacity is insufficient, and releases excess instances once they become idle and meet the reclamation criteria. Administrators can configure baseline capacity, maximum pool size, concurrency thresholds, and idle timeouts to balance responsiveness and resource costs.

The Enterprise Edition also supports multiple replicas of key services. Shared state and coordination of background tasks provide a foundation for horizontal scaling and failure recovery.

### Multi-Tenant Isolation

The Enterprise Edition establishes boundaries for resources, data, and permissions. Administrators can assign dedicated resources or shared resource pools to users and departments. Execution contexts, session histories, and working files are managed separately for each tenant. Authorization and policies determine which Agents, models, skills, and tools each user can access. This allows organizations to operate a shared platform while providing capabilities and resources appropriate to each business need.

### Centralized Management

Manager provides a central console for configuring users, organizations, roles, cluster admission, Agent templates, runtime resources, and quotas. Administrators can combine models, skills, and tools into reusable Agent templates, bind them to target clusters, and grant access to the appropriate users or organizations. This reduces the work of maintaining configurations for individual instances.

Existing identity systems, business APIs, and skill catalogs can also be integrated through supported protocols and extension mechanisms. For example, organizations can connect an enterprise identity provider through OIDC for single sign-on, then assign resource permissions within the platform.

### Security and Auditing

The Enterprise Edition provides layered protection across identities, service connections, and execution environments. It verifies user identities and Agent access permissions before granting access. Internal services can use mutual TLS (mTLS) authentication when enabled. Tool guardrails determine which operations are allowed, require confirmation, or must be denied, while the JiuwenBox sandbox provides a controlled environment for tool and code execution.

The platform supports OpenTelemetry-based observability and structured audit event collection, covering key activities such as user access, sessions, files, sandbox operations, and skill authorization. Combined with an organization's collection, storage, and query infrastructure, these records help diagnose execution problems and trace key operations. Audit fields, collection scope, and export pipelines can be configured for business needs. See [Audit Log (Chinese)](../zh/核心功能/统一管理平台/集群管理/集群配置/审计日志.md) and [End-to-End Security (Chinese)](../zh/核心功能/全链路安全.md).

## Differences from the Personal Edition

The Personal Edition focuses on individual use and exploration in a local working environment. The Enterprise Edition focuses on running shared services for multiple users with centralized governance. Choose the edition that fits your usage model:

| Dimension | Personal Edition | Enterprise Edition |
| --- | --- | --- |
| Primary use cases | Individual productivity, development validation, and exploring new capabilities | Shared Agent services across departments and users, with centralized deployment and operations |
| Deployment | Installed and started on a local computer through the desktop application or command line | Deployed as container images on Linux servers; currently requires a Kubernetes or OpenShift cluster |
| Runtime resources | Tasks run primarily on local machine resources | A distributed runtime manages resource pools and allocates, scales, and reclaims instances according to policy |
| Configuration management | Models, skills, and tools configured for local use | Identities, permissions, capability templates, clusters, and quotas managed centrally through the console |
| Security and isolation | Tool permissions and access controls for a personal environment | Tenant isolation, centralized authorization, sandboxing, and configurable security auditing for shared services |
| Feature focus | Exploring new capabilities and experimental features | Features selected and adapted to enterprise product design, security requirements, and operational needs |

**The Personal and Enterprise Editions share the same code mainline and both evolve on the [develop branch of the JiuwenSwarm repository](https://gitcode.com/openJiuwen/jiuwenswarm/tree/develop).** They share core technologies and collaboration capabilities, but their available features are not identical. The Enterprise Edition adapts, restricts, or does not yet expose a small number of features based on product design and security considerations. To try experimental features, start with the Personal Edition. Feature availability in the Enterprise Edition depends on the release and its accompanying documentation.

## Deployment and Getting Started

**The Personal Edition runs locally.** Install the desktop application, or install and start the service from the command line, then configure a model and begin using it on your computer. A Kubernetes cluster is not required for personal use. See the [JiuwenSwarm installation instructions](https://gitcode.com/openJiuwen/jiuwenswarm/blob/develop/README.md) and the [Personal Edition Quick Start](https://gitcode.com/openJiuwen/jiuwenswarm/blob/develop/docs/en/Quickstart.md).

**The Enterprise Edition currently uses container images deployed on servers.** Prepare Linux servers and a Kubernetes environment before deployment. OpenShift, which is based on Kubernetes, is also supported. Use the matching deployment toolkit and component images to prepare storage, databases, and other infrastructure services, then start the runtime services and management services. Employees access the services from a browser on their own computers without installing a separate runtime environment for each user.

For a first deployment, follow the [Quick Start (Chinese)](../zh/快速开始/快速上手.md) to prepare the environment, start services, authorize users, and complete the first conversation. See the [Cluster Deployment Guide (Chinese)](../zh/快速开始/集群部署指南.md) for deployment parameters, storage integration, component startup, and upgrades. For component responsibilities and interactions, see [Architecture (Chinese)](../zh/整体架构.md).

A lighter local deployment option for the Enterprise Edition is being considered for development validation and small-scale use. It has not been released. Capabilities and availability will be announced by the community, so stay tuned.
