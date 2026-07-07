import GlassCard from "../design/GlassCard";

export default function StubPage({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div>
      <h1 className="page-title">{title}</h1>
      <p className="page-subtitle">{subtitle}</p>
      <GlassCard className="stub-note">Coming in a later phase.</GlassCard>
    </div>
  );
}
