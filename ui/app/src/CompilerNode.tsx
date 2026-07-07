import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

export type CompilerNodeData = {
  label: string;
  displayLabel?: string;
  nodeType: string;
  status: "idle" | "running" | "completed" | "error";
  params?: Record<string, unknown>;
};

function CompilerNode({ data }: NodeProps) {
  const d = data as CompilerNodeData;
  const title = d.displayLabel || d.label;
  return (
    <div className={`compiler-node status-${d.status || "idle"}`}>
      <Handle type="target" position={Position.Top} className="handle" />
      <div className="compiler-node__label">{title}</div>
      <div className="compiler-node__type">{d.nodeType}</div>
      <Handle type="source" position={Position.Bottom} className="handle" />
    </div>
  );
}

export default memo(CompilerNode);
