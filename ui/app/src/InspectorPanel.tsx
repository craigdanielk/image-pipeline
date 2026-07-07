import { useState } from "react";
import type { CompileFile } from "./types";
import { useCreatorMode } from "./CreatorModeContext";

type Props = {
  prompt: string;
  files: CompileFile[];
  selectedFile: string | null;
  artifactText: string;
  manifestHash: string;
  onSelectFile: (path: string) => void;
};

export default function InspectorPanel({
  prompt,
  files,
  selectedFile,
  artifactText,
  manifestHash,
  onSelectFile,
}: Props) {
  const { creatorMode } = useCreatorMode();
  const [tab, setTab] = useState<"prompt" | "technical">("prompt");

  return (
    <aside className="inspector">
      <div className="inspector__tabs">
        <button
          type="button"
          className={tab === "prompt" ? "active" : ""}
          onClick={() => setTab("prompt")}
        >
          Prompt
        </button>
        <button
          type="button"
          className={tab === "technical" ? "active" : ""}
          onClick={() => setTab("technical")}
        >
          Technical
        </button>
      </div>

      {tab === "prompt" && (
        <div className="inspector__prompt">
          {prompt ? (
            <p className="prompt-text">{prompt}</p>
          ) : (
            <p className="empty">Compile a shot to preview the generation prompt.</p>
          )}
        </div>
      )}

      {tab === "technical" && (
        <div className="inspector__technical">
          {creatorMode && manifestHash && (
            <div className="manifest-hash">{manifestHash}</div>
          )}
          <div className="file-list">
            {files.map((f) => (
              <button
                key={f.path}
                type="button"
                className={`file-item ${selectedFile === f.path ? "active" : ""}`}
                onClick={() => onSelectFile(f.path)}
              >
                {f.name}
              </button>
            ))}
            {!files.length && <p className="empty">No compile artifacts yet</p>}
          </div>
          <pre className="artifact-view">{artifactText || "Select a stage file"}</pre>
        </div>
      )}
    </aside>
  );
}
