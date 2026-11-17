// The global write budget: admitted events per UTC hour. FO_BUDGET_PER_HOUR when it is a
// positive integer, else 2000. Read once, when the module loads. Shared by e.js (admission) and
// stats.js (budget use), so both use the same limit.
export function budgetPerHour(raw = process.env.FO_BUDGET_PER_HOUR) {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : 2000;
}
export const BUDGET_PER_HOUR = budgetPerHour();
