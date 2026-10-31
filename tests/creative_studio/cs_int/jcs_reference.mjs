// Independent RFC 8785 reference: ECMAScript itself. JSON.stringify gives the RFC's number
// and string serialisation; Array.prototype.sort() with no comparator orders keys by UTF-16
// code units, exactly as RFC 8785 §3.2.3 requires. Reads a JSON array of documents on stdin,
// writes a JSON array of canonical strings.
const jcs = (v) => {
  if (Array.isArray(v)) return "[" + v.map(jcs).join(",") + "]";
  if (v !== null && typeof v === "object")
    return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + jcs(v[k])).join(",") + "}";
  return JSON.stringify(v);
};
let raw = "";
process.stdin.on("data", (c) => (raw += c)).on("end", () => {
  process.stdout.write(JSON.stringify(JSON.parse(raw).map(jcs)));
});
