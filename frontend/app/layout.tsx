import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Media Search",
  description:
    "Temporal multimodal video search: upload videos, index them with SigLIP embeddings, and search by natural language.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-surface-950 text-slate-200 antialiased">
        {children}
      </body>
    </html>
  );
}
