# Organizations

## Overview

Administrators manage organizations and memberships under **Identity Center → Orgs** in Manager. Create organizations for departments, project teams, or other groups, then configure cluster admission and access to Agents and runtimes by organization. A user can belong to multiple organizations.

## Workflow

1. Sign in to Manager as a platform administrator and go to **Identity Center → Orgs**.
2. Select **New Org**, enter the basic information, and save.
3. On the **Members** tab, add users who are already registered in the identity center. See [Users](Users.md) for account creation steps.
4. Open the target cluster and configure cluster admission and access to Agents and runtimes for the organization. See [Quick Start](../../../Getting%20Started/Quick%20Start.md) for the steps.

## Manager Configuration

### View and Search Organizations

**Org Management** lists each organization's ID, display name, enabled status, and last update time. Search by organization ID or display name, or use the filter in the **Enabled** column.

![Org Management list](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Organizations/org-01-list.png)

### Create an Organization

Select **New Org**, fill in the fields below on the **Basic** tab, and select **Save**. New organizations are enabled by default. After saving, you can switch to **Members**.

![New Org basic information](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Organizations/org-02-create.png)

| Field | Description |
| ---- | ---- |
| **Group ID** | Optional. If blank, the system generates an ID. A custom ID must be unique in the identity center and can contain up to 64 characters. Use only English letters, digits, underscores, and hyphens. The ID is fixed after creation. |
| **Display Name** | Required. Identifies the organization in lists and when configuring cluster admission or resource access. Up to 128 characters. |

### Add and Remove Members

1. Select **Edit** in the organization's row and open the **Members** tab.
2. Select **Add member**. Search by user ID or display name, select one or more users, and select **Confirm**.

   ![Select members to add](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Organizations/org-03-add-member.png)

3. Verify the user IDs and status in the member list. To end a user's membership in this organization, select **Remove** in their row and confirm. The user's account and memberships in other organizations remain active.

   ![Organization details with a member](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Organizations/org-04-members.png)

The system classifies users without any organization membership as **No org**. To give one of these users a membership, add them from the target organization's **Members** tab.

### Edit, Disable, and Delete Organizations

- To rename an organization, select **Edit** in its row, update **Display Name** on the **Basic** tab, and save.
- Turn off the **Enabled** switch to disable an organization. The organization disappears from the organization choices in the user console, and its memberships are preserved. Once it is enabled again, users can use the organization according to the cluster configuration.
- To delete an organization, select **Delete**, verify the organization name, and confirm. Deletion removes the organization's memberships. Before deleting it, check for references to the organization in cluster admission, Agent, and runtime settings. If the organization is bound to an enterprise authentication connection, remove that binding first.

### Export and Import Organizations

1. In the source environment, select the organizations in **Org Management** and select **Export**. The `.xlsx` file contains organization profiles, memberships, and associated user profiles.
2. If a local member referenced by a new organization does not exist in the target environment, fill in a new initial password in the `initial_password` column of the `02_Users` worksheet. For enterprise-authenticated users, establish the corresponding identity in the target environment first. See [Users](Users.md#notes) for how to handle credentials.
3. On the target environment's **Org Management** page, select **Import**, choose the workbook, and review the preflight results. Once preflight passes, select **Import** in the dialog and verify the new organization's member list.

Preflight matches organizations by group ID. If that ID already exists in the target environment, the system reuses the organization and its existing memberships. To merge members, add them on the target organization's **Members** tab. After import, review admission and resource access on the target cluster.

## Use the User Console

After signing in, users can choose from their enabled organizations. To use an Agent under an organization, the target cluster must admit the user or their organization and grant the user access to the Agent and runtime.

If the user console still shows the previous organization after membership changes, sign out and sign back in, then check the user's organizations and cluster access.
