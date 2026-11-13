# Roles

## Overview

A role is a set of permissions. Administrators configure roles under **Identity Center → Roles** in Manager, then assign them to users. A user's permissions come from all their effective assignments. The role must be enabled and the assignment must be within its validity period. Configure cluster admission and resource access for Agents in the target cluster. See [Quick Start](../../../Getting%20Started/Quick%20Start.md) for the steps.

The built-in platform administrator role has the ID `platform_admin`. Its role type, permissions, and enabled status are read-only on the role page. The built-in role cannot be deleted.

## Workflow

1. Sign in to Manager with an account that can view and manage roles, then go to **Identity Center → Roles**.
2. Select **New Role**, fill in the basic information, and choose a role type.
3. On the **Permissions** tab, select the required permissions and save.
4. On the **Assignments** tab, assign the role to users and set an expiration time if needed. Sign in as a target user to verify the operations the role permits.

## Manager Configuration

### View and Search Roles

**Role Management** lists the role ID, name, description, type, permission counts, number of assigned users, enabled status, and last update time. Search by ID, name, or description, or filter by type and enabled status. Viewing roles requires `iam:role:read`. Creating or editing roles and assigning users require `iam:role:write`.

![Role Management list](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Roles/role-01-list.png)

### Create a Role

Select **New Role** and fill in the following fields on the **Basics** tab.

![New Role basic information](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Roles/role-02-create.png)

| Field | Description |
| ---- | ---- |
| **Role ID** | Required. Must be unique. Use up to 64 English letters, digits, underscores, and hyphens. The ID is fixed after creation. |
| **Role Name** | Required. Identifies the role in lists and assignment settings. Up to 128 characters. |
| **Description** | Optional. Describe the role's purpose or intended users. Up to 512 characters. |
| **Role Type** | Required. The current version offers `admin` and `user`. The default is `admin`. The permission list changes with the role type. |

**An effective assignment to an `admin` role grants platform administrator access, even if the role only includes View Roles.** Before assigning it, verify that the user needs access to platform administrator features such as user and organization management.

### Configure Permissions

Open the **Permissions** tab, select the required permissions, and select **Save**. Search by permission ID, name, or description. Clear the search box to see every permission visible for the current role type. The number beside the tab shows how many permissions are selected. After changing the role type, review the selected permissions.

The screenshot shows all eight permissions available for the `admin` role type in the current version when workspace quota is enabled. Manager currently displays the built-in permission names and descriptions in Chinese in the English interface. Use the permission IDs to match the screenshot to the English names below. For the `user` role type, refer to the list shown after switching types.

![All permissions for the admin role type with workspace quota enabled](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Roles/role-03-permissions.png)

| Group | Permission | Purpose |
| ---- | ---- | ---- |
| Quota | **View Quota** `quota:read` | View quota policies, effective limits, and usage. |
| Quota | **Manage Quota** `quota:write` | Create, edit, disable, or delete quota policies. |
| Approvals | **View Approvals** `approval:read` | View the approval list and individual requests. |
| Approvals | **Process Approvals** `approval:act` | Approve or reject requests. |
| Role Management | **View Roles** `iam:role:read` | View roles, permissions, and user assignments. |
| Role Management | **Manage Roles** `iam:role:write` | Create, edit, disable, or delete custom roles, and configure permissions and user assignments. |
| Other | **View Deployment Tasks** `deployment:read` | Reserved for permission-based access to deployment tasks and logs. |
| Other | **Start Deployments** `deployment:execute` | Reserved for permission-based control of starting or canceling deployments and changing default parameters. |

Access to **Deployments** currently requires platform administrator access. The two deployment permissions are reserved for future permission-based authorization.

#### How Feature Flags Affect Permissions

- **Workspace quota**. `WORKSPACE_QUOTA_ENABLED` is off by default. When it is off, the role page hides **View Quota** and **Manage Quota**, and clearing the search box shows the six remaining `admin` permissions. The workspace quota APIs are disabled as well. To enable quota, set `WORKSPACE_QUOTA_ENABLED=true` in `.env.custom` in the enterprise deployment directory and apply the configuration through the deployment process. See section **18. Workspace Quota** in the [Manager API Reference (Chinese)](../../../../zh/开发文档/接口文档/管理面接口文档.md) for the flag requirement and the [Cluster Deployment Guide](../../../Getting%20Started/Cluster%20Deployment%20Guide.md) for deployment steps.
- **Approvals**. **View Approvals** and **Process Approvals** remain available when workspace quota is off. Their respective permissions still control approval lists, details, and actions. Requests to expand workspace quotas require the quota feature to be enabled. See **Workspace Quota** and **Approvals** in the [Manager API Reference (Chinese)](../../../../zh/开发文档/接口文档/管理面接口文档.md) for API permissions and flag requirements.

### Assign a Role to Users

1. After saving the role, open the **Assignments** tab and select **Add Assignment**.
2. Search for and select registered users. For a temporary assignment, set **Expires At** to a future time. All users selected in one batch share the same expiration time. Leave the field empty for an assignment that never expires. See [Users](Users.md) for account creation steps.

   ![Add a role assignment with an expiration time](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Roles/role-04-grant.png)

3. Select **Confirm** and verify the user ID, who granted the role, and the expiration time.

   ![Role assignment list](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Roles/role-05-assignees.png)

To extend an assignment or make it permanent, select **Edit** in the user's row, update or clear the expiration time, select **Save**, and verify the result in the list.

An expired assignment stops granting permissions. To revoke it early, select **Remove** in the user's row and confirm. Permissions from the user's other effective role assignments remain in effect.

### Edit, Disable, and Delete Roles

- Select **Edit** in a custom role's row to update its basic information or permissions. The saved permissions apply to all effective assignments of that role.
- Turn off the **Enabled** switch to disable a custom role. It stops granting permissions while disabled, and its assignment records are preserved. Enabling it again restores any assignments that are still within their validity period.
- To delete a custom role, select **Delete** in its row, verify the role ID, and confirm. Deleting the role removes its user assignments.

### Export and Import Roles

1. As a platform administrator, select the roles in the source environment's role list and select **Export**. The `.xlsx` file includes roles, permissions, user assignments, and associated user profiles.
2. Verify the permissions and users in the workbook for the target environment. For local users missing from the target environment, fill in new initial passwords in the `initial_password` column of `02_Users`. For enterprise-authenticated users, establish the corresponding identity in the target environment first.
3. On the target environment's **Role Management** page, select **Import**, choose the workbook, and review the preflight results. Identical roles can be reused and missing assignments can be added. Resolve conflicts before importing if a role with the same ID has different configuration or permissions, or if the same user's assignment has a different expiration time. A built-in role can be reused if the same configuration already exists in the target environment.
4. Once preflight passes, select **Import** in the dialog and verify the role's permissions, assigned users, and expiration times. Before adding an `admin` assignment, confirm that the user has the corresponding administrative responsibilities.
