import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import CompilerNode from "./CompilerNode";

const nodeTypes = { compilerNode: CompilerNode };

type Props = {
  nodes: Node[];
  edges: Edge[];
  emptyMessage?: string;
};

export default function GraphCanvas({
  nodes,
  edges,
  emptyMessage = "Compile a shot to see the workflow",
}: Props) {
  return (
    <section className="canvas-panel">
      <div className="canvas-panel__surface">
        {nodes.length ? (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1}
              color="var(--color-glass-border)"
            />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor="var(--color-minimap-node)"
              maskColor="var(--color-overlay-scrim)"
              style={{ background: "var(--color-minimap-bg)" }}
            />
          </ReactFlow>
        ) : (
          <div className="canvas-empty">
            <span className="canvas-empty__icon">◇</span>
            <p>{emptyMessage}</p>
          </div>
        )}
      </div>
    </section>
  );
}
