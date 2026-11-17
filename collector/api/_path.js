// Vercel also sends /api/e/, /api/e.js, /api/e/x and the like to the function in api/e.js. A
// Vercel Firewall rule that matches the exact path does not cover those aliases, so each handler
// answers only on its exact path (the query string does not matter) and gives 404 on any other.
// Files whose name starts with "_" are not routes on Vercel.
export function exactPath(request, path) {
  return new URL(request.url).pathname === path;
}
