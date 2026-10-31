// Numeric/keyboard equivalents for every layer control. Edits produce a new
// working document; nothing is sent until the user saves a revision.
import type { Artboard, Layer, Transform } from "./geometry";

type Props = { board: Artboard; layer: Layer; onChange: (next: Layer) => void };

function Num({ label, value, onChange, step = 1, min, max }: { label: string; value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number }) {
  return (
    <label className="cs-field">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(v);
        }}
      />
    </label>
  );
}

export function LayerInspector({ layer, onChange }: Props) {
  const t = layer.transform;
  const setT = (patch: Partial<Transform>) => onChange({ ...layer, transform: { ...t, ...patch } } as Layer);
  const locked = layer.locked;
  return (
    <section className="cs-inspector" aria-label={`Layer ${layer.name}`}>
      <h4>
        {layer.name} <span className="cs-muted">({layer.type})</span>
      </h4>
      <label className="cs-field">
        <span>Locked</span>
        <input type="checkbox" checked={locked} onChange={(e) => onChange({ ...layer, locked: e.target.checked } as Layer)} />
      </label>
      <fieldset disabled={locked}>
        <legend>Position &amp; size (artboard px)</legend>
        <Num label="X" value={t.x} onChange={(x) => setT({ x })} />
        <Num label="Y" value={t.y} onChange={(y) => setT({ y })} />
        <Num label="Width" value={t.width} min={1} onChange={(width) => setT({ width })} />
        <Num label="Height" value={t.height} min={1} onChange={(height) => setT({ height })} />
        {layer.type !== "logo" ? (
          <>
            <Num label="Rotation°" value={t.rotation_deg} min={-360} max={360} onChange={(rotation_deg) => setT({ rotation_deg })} />
            <Num label="Opacity" value={t.opacity} step={0.05} min={0} max={1} onChange={(opacity) => setT({ opacity })} />
          </>
        ) : (
          <p className="cs-muted">Logos keep their approved proportions: contain-fit, unrotated, full opacity.</p>
        )}
      </fieldset>
      {layer.type === "text" ? (
        <fieldset disabled={locked}>
          <legend>Exact copy</legend>
          <label className="cs-field cs-field-wide">
            <span>Text {layer.content.copy_id ? `(copy: ${layer.content.copy_id})` : "(unbound — needs a copy version before export)"}</span>
            <textarea
              value={layer.content.text}
              rows={3}
              onChange={(e) => onChange({ ...layer, content: { ...layer.content, text: e.target.value } })}
            />
          </label>
          <Num
            label="Size px"
            value={layer.content.style.font_size_px}
            min={1}
            onChange={(font_size_px) => onChange({ ...layer, content: { ...layer.content, style: { ...layer.content.style, font_size_px } } })}
          />
          <Num
            label="Line height"
            value={layer.content.style.line_height}
            step={0.05}
            min={0.5}
            max={5}
            onChange={(line_height) => onChange({ ...layer, content: { ...layer.content, style: { ...layer.content.style, line_height } } })}
          />
          <label className="cs-field">
            <span>Align</span>
            <select
              value={layer.content.style.align}
              onChange={(e) =>
                onChange({ ...layer, content: { ...layer.content, style: { ...layer.content.style, align: e.target.value as "left" | "center" | "right" } } })
              }
            >
              <option value="left">Left</option>
              <option value="center">Center</option>
              <option value="right">Right</option>
            </select>
          </label>
          <p className="cs-muted">Font: exact brand file <code>{layer.content.font.sha256.slice(0, 12)}</code> · weight {layer.content.style.weight}</p>
        </fieldset>
      ) : null}
      {layer.type === "shape" ? (
        <fieldset disabled={locked}>
          <legend>Shape</legend>
          <label className="cs-field">
            <span>Fill</span>
            <input value={layer.content.fill} onChange={(e) => onChange({ ...layer, content: { ...layer.content, fill: e.target.value } })} />
          </label>
        </fieldset>
      ) : null}
    </section>
  );
}
