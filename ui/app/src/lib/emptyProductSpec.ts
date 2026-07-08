import type { ProductSpecV2 } from "../types";

export function emptyProductSpec(id: string, name: string): ProductSpecV2 {
  return {
    schema_version: "product-spec-v2",
    id,
    name,
    immutable: true,
    product: {
      geometry: { description: "", symmetry: "", envelope: "" },
      materials: [{ id: "primary", type: "", color: "", finish: "", texture: "" }],
      dimensions: {},
    },
    brand: { logos: [] },
    constraints: { preserve_geometry: true, preserve_material: true, preserve_proportions: true },
    editing: { protected_regions: [], editable_regions: [] },
    references: [],
  };
}
