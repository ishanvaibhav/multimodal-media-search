"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { Button } from "./primitives";
import { Modal } from "./Modal";

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Delete",
  requireTypedConfirmation,
  children,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  requireTypedConfirmation?: string;
  children?: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const confirmed = requireTypedConfirmation
    ? typed === requireTypedConfirmation
    : true;

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <p className="mb-4 text-sm text-slate-300">{description}</p>
      {children}
      {requireTypedConfirmation && (
        <div className="mb-4">
          <label className="label">
            Type <span className="font-mono text-red-400">{requireTypedConfirmation}</span> to
            confirm
          </label>
          <input
            className="input font-mono"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={requireTypedConfirmation}
          />
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="danger"
          disabled={!confirmed}
          onClick={() => {
            onConfirm();
            setTyped("");
            onClose();
          }}
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
