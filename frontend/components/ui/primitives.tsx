"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-slate-500 border-t-accent"
      style={{ width: size, height: size }}
      aria-label="loading"
    />
  );
}

export function Button({
  children,
  variant = "ghost",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
}) {
  const cls =
    variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : "btn-ghost";
  return (
    <button className={cls} {...rest}>
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "green" | "red" | "amber" | "blue" | "slate";
}) {
  const tones: Record<string, string> = {
    green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    red: "bg-red-500/15 text-red-300 border-red-500/30",
    amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    blue: "bg-accent/15 text-accent-soft border-accent/30",
    slate: "bg-surface-700 text-slate-300 border-surface-600",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function ProgressBar({
  value,
  tone = "accent",
}: {
  value: number;
  tone?: "accent" | "green" | "amber" | "red";
}) {
  const pct = Math.max(0, Math.min(100, value));
  const tones: Record<string, string> = {
    accent: "bg-accent",
    green: "bg-emerald-500",
    amber: "bg-amber-500",
    red: "bg-red-500",
  };
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-surface-700">
      <div
        className={`h-full rounded-full transition-all duration-300 ${tones[tone]}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2 disabled:opacity-50"
    >
      <span
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
          checked ? "bg-accent" : "bg-surface-600"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : "translate-x-0.5"
          }`}
        />
      </span>
      <span className="text-xs text-slate-300">{label}</span>
    </button>
  );
}

export function EmptyState({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <div className="text-3xl">🗂️</div>
      <p className="text-sm font-medium text-slate-200">{title}</p>
      {subtitle && <p className="max-w-md text-xs text-slate-400">{subtitle}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`panel p-4 ${className}`}>{children}</div>;
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <span className="text-sm font-semibold text-slate-200">{value}</span>
    </div>
  );
}
