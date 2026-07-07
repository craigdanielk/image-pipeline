import GlassCard from "../design/GlassCard";

const STAGES = [
  "ProductSpec",
  "CreativeSpec",
  "EditSpec",
  "Prompt Compiler",
  "Capability Resolver",
  "Execution Plan",
  "Graph Runtime",
  "Provider",
  "Assets",
];

export default function PipelinesPage() {
  return (
    <div>
      <h1 className="page-title">Pipelines</h1>
      <p className="page-subtitle">Read-only compile chain — developers only.</p>
      <GlassCard className="pipeline-chain">
        {STAGES.map((s, i) => (
          <div key={s} className="pipeline-chain__row">
            {i > 0 && <span className="pipeline-chain__arrow">↓</span>}
            <span className="pipeline-chain__stage">{s}</span>
          </div>
        ))}
      </GlassCard>
    </div>
  );
}
