import type { ReactNode } from "react";

type Props = {
  variant?: "dots" | "mesh";
  children?: ReactNode;
};

/** Layer 1 — living context beneath glass floats */
export default function ContextCanvas({ variant = "dots", children }: Props) {
  return (
    <div className={`context-canvas context-canvas--${variant}`} aria-hidden={!children}>
      {children}
    </div>
  );
}
