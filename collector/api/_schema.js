// The allowlist, shared with the client (tests/test_package.py keeps schema.json equal to it).
import schema from "../schema.json" with { type: "json" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const STRINGS = Object.fromEntries(Object.entries(schema.strings).map(([k, v]) => [k, new RegExp(v)]));

// Returns the cleaned event, or null when the request is not a valid event.
export function clean(body, publicKey) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  if (!publicKey || body.api_key !== publicKey) return null;
  if (typeof body.event !== "string" || !body.event.startsWith("fo_")) return null;
  const event = body.event.slice(3);
  if (!schema.events.includes(event)) return null;
  if (typeof body.distinct_id !== "string" || !UUID.test(body.distinct_id)) return null;
  const p = body.properties;
  if (!p || typeof p !== "object" || p.schema !== schema.schema) return null;
  const out = {};
  for (const [k, v] of Object.entries(p)) {
    if (Object.hasOwn(schema.enums, k)) { if (schema.enums[k].includes(v)) out[k] = v; }
    else if (schema.ints.includes(k)) { if (Number.isInteger(v) && v >= 0 && v <= 10000) out[k] = v; }
    else if (schema.bools.includes(k)) { if (typeof v === "boolean") out[k] = v; }
    else if (schema.via_keys.includes(k)) { if (schema.via.includes(v)) out[k] = v; }
    else if (Object.hasOwn(STRINGS, k)) { if (typeof v === "string" && STRINGS[k].test(v)) out[k] = v; }
  }
  return { event, id: body.distinct_id, props: out };
}
