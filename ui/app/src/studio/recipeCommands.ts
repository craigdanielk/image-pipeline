// Typed semantic edits for cs-recipe-command-v1. Pure functions: no provider
// calls, no hidden defaults. Each applied command returns its inverse so undo
// is a new edit of the draft (saved revisions are never rewritten).
import type { Recipe, RecipeCommandBatch, VersionRef } from "./contracts/types.gen";

export type RecipeCommand = RecipeCommandBatch["commands"][number];
export type RecipeNode = Recipe["nodes"][number];

export const recipeCommands = {
  setRequest: (request: VersionRef): RecipeCommand => ({ op: "set_request", request }),
  setTreatment: (treatment: VersionRef): RecipeCommand => ({ op: "set_treatment", treatment }),
  setTemplate: (template: VersionRef | null): RecipeCommand => ({ op: "set_template", template }),
  upsertNode: (node: RecipeNode): RecipeCommand => ({ op: "upsert_node", node }),
  removeNode: (node_id: string): RecipeCommand => ({ op: "remove_node", node_id }),
  setOutputs: (outputs: Recipe["outputs"]): RecipeCommand => ({ op: "set_outputs", outputs }),
  setComposition: (composition: VersionRef | null): RecipeCommand => ({ op: "set_composition", composition }),
  setAuthoringView: (authoring_view: Recipe["authoring_view"]): RecipeCommand => ({
    op: "set_authoring_view",
    authoring_view,
  }),
};

export class RecipeCommandError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
  }
}

/** Apply one command; returns the next recipe working copy and the inverse command(s). */
export function applyCommand(recipe: Recipe, cmd: RecipeCommand): { next: Recipe; inverse: RecipeCommand[] } {
  switch (cmd.op) {
    case "set_request":
      return { next: { ...recipe, request: cmd.request }, inverse: [recipeCommands.setRequest(recipe.request)] };
    case "set_treatment":
      return {
        next: { ...recipe, treatment: cmd.treatment },
        inverse: [recipeCommands.setTreatment(recipe.treatment)],
      };
    case "set_template":
      return { next: { ...recipe, template: cmd.template }, inverse: [recipeCommands.setTemplate(recipe.template)] };
    case "upsert_node": {
      const prior = recipe.nodes.find((n) => n.id === cmd.node.id);
      const nodes = prior
        ? recipe.nodes.map((n) => (n.id === cmd.node.id ? cmd.node : n))
        : [...recipe.nodes, cmd.node];
      return {
        next: { ...recipe, nodes },
        inverse: [prior ? recipeCommands.upsertNode(prior) : recipeCommands.removeNode(cmd.node.id)],
      };
    }
    case "remove_node": {
      const prior = recipe.nodes.find((n) => n.id === cmd.node_id);
      if (!prior) throw new RecipeCommandError("unknown_node", `No step ${cmd.node_id} in this recipe.`);
      const dependents = recipe.nodes.filter((n) =>
        n.inputs.some((i) => i.source.kind === "node" && i.source.node_id === cmd.node_id),
      );
      if (dependents.length) {
        throw new RecipeCommandError(
          "node_has_dependents",
          `Step ${cmd.node_id} feeds ${dependents.map((d) => d.id).join(", ")}; disconnect them first.`,
        );
      }
      return {
        next: { ...recipe, nodes: recipe.nodes.filter((n) => n.id !== cmd.node_id) },
        inverse: [recipeCommands.upsertNode(prior)],
      };
    }
    case "set_outputs":
      return { next: { ...recipe, outputs: cmd.outputs }, inverse: [recipeCommands.setOutputs(recipe.outputs)] };
    case "set_composition":
      return {
        next: { ...recipe, composition: cmd.composition },
        inverse: [recipeCommands.setComposition(recipe.composition)],
      };
    case "set_authoring_view":
      return {
        next: { ...recipe, authoring_view: cmd.authoring_view },
        inverse: [recipeCommands.setAuthoringView(recipe.authoring_view)],
      };
  }
}

export function applyCommands(recipe: Recipe, cmds: RecipeCommand[]): { next: Recipe; inverse: RecipeCommand[] } {
  let next = recipe;
  const inverse: RecipeCommand[] = [];
  for (const cmd of cmds) {
    const step = applyCommand(next, cmd);
    next = step.next;
    inverse.unshift(...step.inverse);
  }
  return { next, inverse };
}

/** View metadata only: never changes compile hash or spend (contract.md §7). */
export function isPresentationOnly(cmds: RecipeCommand[]): boolean {
  return cmds.every((c) => c.op === "set_authoring_view");
}

export function recipeRef(recipe: Recipe): VersionRef {
  return {
    authority: "creative-studio",
    workspace_id: recipe.workspace_id,
    kind: "recipe",
    id: recipe.id,
    version_id: recipe.version_id,
    revision: recipe.revision,
    sha256: recipe.sha256,
  };
}
