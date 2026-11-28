import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import './InlineEditorContext.css';

interface InlineEditorStack {
  target: HTMLDivElement | null;
  active: boolean;
  activeId: string | undefined;
  register: (id: string) => void;
  unregister: (id: string) => void;
}

const InlineEditorContext = createContext<InlineEditorStack | null>(null);

/** Places existing editors in the wizard workspace; nested editors keep their parent draft mounted. */
export function InlineEditorProvider({ children, active = true }: { children: ReactNode; active?: boolean }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [stack, setStack] = useState<string[]>([]);
  const register = useCallback((id: string) => setStack(current => current.includes(id) ? current : [...current, id]), []);
  const unregister = useCallback((id: string) => setStack(current => current.filter(item => item !== id)), []);
  const value = useMemo(() => ({ target, active, activeId: stack[stack.length - 1], register, unregister }), [target, active, stack, register, unregister]);
  return <InlineEditorContext.Provider value={value}>
    {children}
    <div ref={setTarget} className="inline-editor-stage" />
  </InlineEditorContext.Provider>;
}

export function useInlineEditor() {
  return useContext(InlineEditorContext);
}

interface EditorPresentation {
  readOnly?: boolean;
  footer?: ReactNode;
}
const EditorPresentationContext = createContext<EditorPresentation>({});

/** Shows a complete existing form as a read-only resource preview without maintaining a second form. */
export function EditorPresentationProvider({ children, readOnly, footer }: EditorPresentation & { children: ReactNode }) {
  const value = useMemo(() => ({ readOnly, footer }), [readOnly, footer]);
  return <EditorPresentationContext.Provider value={value}>{children}</EditorPresentationContext.Provider>;
}

export function useEditorPresentation() {
  return useContext(EditorPresentationContext);
}
