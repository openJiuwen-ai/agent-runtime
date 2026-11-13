# Users

## Overview

Administrators maintain the JiuwenSwarm user directory under **Identity Center → Users** in Manager. They can create local accounts, change display names, reset local passwords, enable or disable accounts, and create, import, or export users in bulk.

After users join an organization, administrators can configure cluster admission and resource access for the organization as a group. To give a user access to Manager, assign the appropriate role under **Identity Center → Roles**.

## Workflow

1. Sign in to Manager as a platform administrator and go to **Identity Center → Users**.
2. Select **New User** to create an account, or **Batch Create** to create multiple accounts at once.
3. To grant access by organization, go to **Identity Center → Orgs**, open the target organization, and add users on the **Members** tab.
4. Configure admission to the target cluster for the user or their organization. To enable Agent conversations, configure Agent and runtime access for the user as well. See [Quick Start](../../../Getting%20Started/Quick%20Start.md) for the steps.

## Manager Configuration

### View and Search Users

**User Management** lists each user's ID, display name, enabled status, and last update time. Use the search box to find a user by ID or display name, or use the filter in the **Enabled** column to show enabled or disabled users. Select **Refresh** to reload the list.

![User Management list](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Users/user-01-list.png)

### Create a User

Select **New User**, fill in the fields below, and select **Save**. Search for the user ID in the list to find the new account. New accounts are enabled by default.

![New User form](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Users/user-02-create.png)

| Field | Description |
| ---- | ---- |
| **User ID (for login)** | Required. Serves as the local sign-in name and the unique identifier in the user directory. Up to 64 characters. Use only English letters, digits, underscores, and hyphens. The ID is fixed after creation. |
| **Display Name** | Required. Identifies the user in lists and member pickers. Up to 128 characters. |
| **Password** | Required. The initial password for the local account, up to 256 characters. Share it with the user through a secure channel. |

### Create Users in Bulk

1. Select **Batch Create → Download Template** to download `users_template.xlsx`.
2. Fill in the columns from the template. You can upload an `.xlsx` file or a UTF-8-encoded `.csv` file with up to 500 user rows per submission.

   | Column | Description |
   | ---- | ---- |
   | `username` | Required. The user ID follows the same rules as for a single account. |
   | `password` | Required. The initial password. |
   | `display_name` | Optional. If blank, the display name defaults to the user ID. |

3. Upload the file and review the preview. If the preview reports missing usernames or passwords, or invalid username formats, correct the file and upload it again.

   ![Batch Create preview](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Users/user-04-batch.png)

4. Select **Import** and review the success and failure counts and the result for each row. If some rows fail because of duplicate IDs or other errors, correct and resubmit the failed rows.

If the dialog lists an `orgs` column, follow the downloaded template. After creating the users, set their organization membership on the target organization's **Members** tab.

### Edit, Disable, and Delete Users

- To edit a user, select **Edit** in their row. Change the display name, reset the local password, or set the status to `active` or `disabled`. If you are changing other fields only, leave **Reset password (blank = keep)** empty and select **Save**.
- Use the switch in the **Enabled** column to enable or disable a user. A disabled user cannot sign in again or refresh their credentials. Turning the switch back on restores sign-in eligibility. Access to the user console is still governed by cluster admission rules.
- To delete a user, select **Delete** in their row. Verify the display name and user ID in the dialog and select **Confirm**. Deleting a user removes the account, local credentials, and organization memberships from the identity center. To suspend the account temporarily, use the **Enabled** switch.

![Edit User form](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Users/user-03-edit.png)

### Export and Import User Profiles

Use **Export** and **Import** at the top of the user list to migrate user profiles between environments.

1. In the source environment, select the users to migrate and select **Export** to download an `.xlsx` file. The `initial_password` column in the exported file is blank.
2. For local users that do not yet exist in the target environment, enter new initial passwords in the `initial_password` column of the `02_Users` worksheet. For enterprise-authenticated users, establish the corresponding identity in the target environment first.
3. On the target environment's **User Management** page, select **Import** and choose the `.xlsx` file. The preflight check matches reusable accounts by user ID and preserves their existing profiles and passwords. Resolve any reported conflicts, missing passwords, or missing enterprise-authenticated users before selecting the file again.
4. When preflight passes, select **Import** in the dialog and verify the counts of created and reused accounts. After migration, review organization memberships, role assignments, and cluster admission in the target environment.

   ![User profile import preflight](../../../assets/Core%20Features/Unified%20Management%20Platform/Identity%20Center/Users/user-05-import.png)

## Use the User Console

Users enter their user ID and password on the user console sign-in page. Cluster admission and resource access determine which Agents they can use. If the deployment integrates an enterprise authentication service, users with enterprise accounts sign in through the enterprise option on the sign-in page. Their passwords are managed by the enterprise authentication service.

If an enabled account has no accessible clusters, check whether the target cluster admits the user or their organization and whether an admission rule has expired or a deny rule applies. If the user can access the cluster but cannot start a conversation, check whether the Agent and runtime access rules include that user.

## Notes

- Batch creation files and import files with a filled `initial_password` column contain plaintext initial passwords. Restrict access to these files and handle them after import according to your organization's credential management requirements.
