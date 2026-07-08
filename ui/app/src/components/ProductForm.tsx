import type { ProductSpecV2, Material, Logo } from "../types";

type Props = {
  value: ProductSpecV2;
  onChange: (next: ProductSpecV2) => void;
  disabledId?: boolean;
};

function StringListEditor({
  label,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="field-label">{label}</label>
      {values.map((v, i) => (
        <div key={i} style={{ display: "flex", gap: "0.4rem", marginBottom: "0.35rem" }}>
          <input
            className="field-input"
            value={v}
            placeholder={placeholder}
            onChange={(e) => {
              const next = [...values];
              next[i] = e.target.value;
              onChange(next);
            }}
          />
          <button type="button" className="btn btn--sm" onClick={() => onChange(values.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={() => onChange([...values, ""])}>
        + Add
      </button>
    </div>
  );
}

export default function ProductForm({ value, onChange, disabledId = false }: Props) {
  const geometry = value.product.geometry;
  const materials = value.product.materials || [];
  const dimensions = value.product.dimensions || {};
  const logos = value.brand?.logos || [];
  const constraints = value.constraints;
  const editing = value.editing;

  const set = (patch: Partial<ProductSpecV2>) => onChange({ ...value, ...patch });

  const setMaterial = (i: number, patch: Partial<Material>) => {
    const next = materials.map((m, j) => (j === i ? { ...m, ...patch } : m));
    set({ product: { ...value.product, materials: next } });
  };
  const addMaterial = () =>
    set({
      product: {
        ...value.product,
        materials: [...materials, { id: `material-${materials.length + 1}`, type: "", color: "", finish: "", texture: "" }],
      },
    });
  const removeMaterial = (i: number) =>
    set({ product: { ...value.product, materials: materials.filter((_, j) => j !== i) } });

  const dimensionEntries = Object.entries(dimensions);
  const setDimension = (key: string, newKey: string, val: string) => {
    const next: Record<string, string | number> = {};
    for (const [k, v] of dimensionEntries) next[k === key ? newKey : k] = k === key ? val : v;
    if (!(newKey in next)) next[newKey] = val;
    set({ product: { ...value.product, dimensions: next } });
  };
  const addDimension = () => set({ product: { ...value.product, dimensions: { ...dimensions, "": "" } } });
  const removeDimension = (key: string) => {
    const next = { ...dimensions };
    delete next[key];
    set({ product: { ...value.product, dimensions: next } });
  };

  const setLogo = (i: number, patch: Partial<Logo>) => {
    const next = logos.map((l, j) => (j === i ? { ...l, ...patch } : l));
    set({ brand: { ...value.brand, logos: next } });
  };
  const addLogo = () =>
    set({
      brand: {
        ...value.brand,
        logos: [...logos, { id: `logo-${logos.length + 1}`, placement: { region: "" }, scale: 1 }],
      },
    });
  const removeLogo = (i: number) => set({ brand: { ...value.brand, logos: logos.filter((_, j) => j !== i) } });

  return (
    <div className="structured-form">
      <div>
        <label className="field-label" htmlFor="pf-name">
          Product name
        </label>
        <input
          id="pf-name"
          className="field-input"
          value={value.name}
          onChange={(e) => set({ name: e.target.value })}
          required
        />
      </div>
      <div>
        <label className="field-label" htmlFor="pf-id">
          ID (slug)
        </label>
        <input id="pf-id" className="field-input" value={value.id} disabled={disabledId} readOnly={disabledId} />
      </div>

      <h2 className="dashboard-panel__title">Geometry</h2>
      <div>
        <label className="field-label" htmlFor="pf-geo-desc">
          Description
        </label>
        <textarea
          id="pf-geo-desc"
          className="field-input field-textarea"
          rows={3}
          value={geometry.description}
          onChange={(e) =>
            set({ product: { ...value.product, geometry: { ...geometry, description: e.target.value } } })
          }
          required
        />
      </div>
      <div style={{ display: "flex", gap: "0.75rem" }}>
        <div style={{ flex: 1 }}>
          <label className="field-label" htmlFor="pf-geo-sym">
            Symmetry
          </label>
          <input
            id="pf-geo-sym"
            className="field-input"
            value={geometry.symmetry || ""}
            onChange={(e) => set({ product: { ...value.product, geometry: { ...geometry, symmetry: e.target.value } } })}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label className="field-label" htmlFor="pf-geo-env">
            Envelope
          </label>
          <input
            id="pf-geo-env"
            className="field-input"
            value={geometry.envelope || ""}
            onChange={(e) => set({ product: { ...value.product, geometry: { ...geometry, envelope: e.target.value } } })}
          />
        </div>
      </div>

      <h2 className="dashboard-panel__title">Materials</h2>
      {materials.map((m, i) => (
        <div key={i} className="glass-card" style={{ padding: "0.75rem", marginBottom: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {(["id", "type", "color", "finish", "texture"] as const).map((field) => (
              <div key={field} style={{ flex: "1 1 120px" }}>
                <label className="field-label">{field}</label>
                <input
                  className="field-input"
                  value={(m[field] as string) || ""}
                  onChange={(e) => setMaterial(i, { [field]: e.target.value } as Partial<Material>)}
                />
              </div>
            ))}
          </div>
          <button type="button" className="btn btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => removeMaterial(i)}>
            Remove material
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addMaterial}>
        + Add material
      </button>

      <h2 className="dashboard-panel__title">Dimensions</h2>
      {dimensionEntries.map(([k, v]) => (
        <div key={k} style={{ display: "flex", gap: "0.4rem", marginBottom: "0.35rem" }}>
          <input className="field-input" placeholder="key" value={k} onChange={(e) => setDimension(k, e.target.value, String(v))} />
          <input className="field-input" placeholder="value" value={String(v)} onChange={(e) => setDimension(k, k, e.target.value)} />
          <button type="button" className="btn btn--sm" onClick={() => removeDimension(k)}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addDimension}>
        + Add dimension
      </button>

      <h2 className="dashboard-panel__title">Brand / logos</h2>
      {logos.map((l, i) => (
        <div key={i} className="glass-card" style={{ padding: "0.75rem", marginBottom: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 100px" }}>
              <label className="field-label">id</label>
              <input className="field-input" value={l.id} onChange={(e) => setLogo(i, { id: e.target.value })} />
            </div>
            <div style={{ flex: "1 1 100px" }}>
              <label className="field-label">placement region</label>
              <input
                className="field-input"
                value={l.placement?.region || ""}
                onChange={(e) => setLogo(i, { placement: { ...l.placement, region: e.target.value } })}
              />
            </div>
            <div style={{ flex: "1 1 80px" }}>
              <label className="field-label">scale</label>
              <input
                className="field-input"
                type="number"
                step="0.1"
                value={l.scale ?? 1}
                onChange={(e) => setLogo(i, { scale: Number(e.target.value) })}
              />
            </div>
          </div>
          <button type="button" className="btn btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => removeLogo(i)}>
            Remove logo
          </button>
        </div>
      ))}
      <button type="button" className="btn btn--sm" onClick={addLogo}>
        + Add logo
      </button>

      <h2 className="dashboard-panel__title">Constraints</h2>
      <div style={{ display: "flex", gap: "1rem" }}>
        {(["preserve_geometry", "preserve_material", "preserve_proportions"] as const).map((key) => (
          <label key={key} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}>
            <input
              type="checkbox"
              checked={constraints[key]}
              onChange={(e) => set({ constraints: { ...constraints, [key]: e.target.checked } })}
            />
            {key}
          </label>
        ))}
      </div>

      <h2 className="dashboard-panel__title">Editing regions</h2>
      <StringListEditor
        label="Protected regions"
        values={editing.protected_regions}
        onChange={(next) => set({ editing: { ...editing, protected_regions: next } })}
        placeholder="e.g. logo, band"
      />
      <StringListEditor
        label="Editable regions"
        values={editing.editable_regions}
        onChange={(next) => set({ editing: { ...editing, editable_regions: next } })}
        placeholder="e.g. background, scene"
      />
    </div>
  );
}
