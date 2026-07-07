import type { Output } from "./types";

type Props = {
  outputs: Output[];
  onRefresh?: () => void;
};

export default function OutputStrip({ outputs, onRefresh }: Props) {
  return (
    <section className="output-strip">
      <div className="output-strip__header">
        <h2 className="output-strip__title">Outputs</h2>
        {onRefresh && (
          <button type="button" className="btn btn--sm" onClick={onRefresh}>
            Refresh
          </button>
        )}
      </div>
      <div className="output-strip__filmstrip">
        {outputs.map((item) => (
          <figure key={item.path} className="output-card">
            <a href={item.url} target="_blank" rel="noopener noreferrer">
              <img src={item.url} alt={item.name} loading="lazy" />
            </a>
            <figcaption>{item.name}</figcaption>
          </figure>
        ))}
        {!outputs.length && (
          <p className="empty output-strip__empty">
            Generated images will appear here after you run Generate.
          </p>
        )}
      </div>
    </section>
  );
}
