import { useSyncExternalStore } from 'react';
import { dismissToast, getToasts, subscribeToasts, type Toast } from '../lib/toast';
import { AlertTriangleIcon, CheckCircleIcon, InfoIcon } from './icons';

export function ToastStack() {
  const toasts: Toast[] = useSyncExternalStore(subscribeToasts, getToasts);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.kind}`} onClick={() => dismissToast(toast.id)}>
          <span className="toast-icon">
            {toast.kind === 'success' ? <CheckCircleIcon size={16} /> : toast.kind === 'error' ? <AlertTriangleIcon size={16} /> : <InfoIcon size={16} />}
          </span>
          <span className="toast-message">{toast.message}</span>
        </div>
      ))}
    </div>
  );
}
