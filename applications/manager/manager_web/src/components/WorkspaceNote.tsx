import type { ReactNode } from 'react';

/**
 * Shared explanatory banner used at the end of a workspace. Keeping it below
 * the operational content lets users act first and read the product rule when
 * they need context, while keeping the visual language consistent everywhere.
 */
export function WorkspaceNote({
  title,
  children,
  action,
  className = '',
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`workspace-note card ${className}`} aria-label={title}>
      <div className="workspace-note__icon" aria-hidden="true">◇</div>
      <div className="min-w-0 flex-1">
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {action}
    </section>
  );
}
