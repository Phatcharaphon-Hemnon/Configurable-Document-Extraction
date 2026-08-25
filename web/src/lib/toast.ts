export type ToastKind = 'success' | 'error' | 'info';

export type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
};

type Listener = (toasts: Toast[]) => void;

let toasts: Toast[] = [];
let listeners: Listener[] = [];
let nextId = 1;

function emit() {
  for (const listener of listeners) listener(toasts);
}

export function getToasts(): Toast[] {
  return toasts;
}

export function subscribeToasts(listener: Listener): () => void {
  listeners = [...listeners, listener];
  listener(toasts);
  return () => {
    listeners = listeners.filter((l) => l !== listener);
  };
}

export function pushToast(kind: ToastKind, message: string, ttlMs = 4500) {
  const toast: Toast = { id: nextId++, kind, message };
  toasts = [...toasts, toast];
  emit();
  window.setTimeout(() => dismissToast(toast.id), ttlMs);
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}
