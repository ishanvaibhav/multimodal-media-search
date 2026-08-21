"use client";

import type { ReactNode } from "react";

export function Modal({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={`panel max-h-[90vh] w-full overflow-hidden ${wide ? "max-w-4xl" : "max-w-xl"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-header">
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-white"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="max-h-[calc(90vh-57px)] overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
