/**
 * Cloudflare Access — JWT verification and user profile lookup.
 *
 * How it works:
 *   1. Cloudflare Access sits in front of this Worker (configured in the
 *      Zero Trust dashboard). The Access application's policy is the
 *      authoritative allow-list — only emails it permits can request a
 *      login code and reach the Worker at all.
 *   2. When an authenticated user's browser hits the Worker, Access
 *      injects a signed JWT into the `Cf-Access-Jwt-Assertion` header.
 *      We verify that JWT against Access's public keys (JWKS). Without
 *      verification, a malicious caller could bypass Access by hitting
 *      the Worker URL directly with a forged header.
 *   3. We upsert the user's profile in the `users` D1 table — new users
 *      default to role='member' with name=NULL (the frontend prompts
 *      them to set their name on first login). This table is NOT an
 *      allow-list; it's a profile cache.
 *
 * Required Worker secrets (set with `wrangler secret put`):
 *   ACCESS_TEAM_DOMAIN  e.g. "yourteam.cloudflareaccess.com"
 *   ACCESS_AUD          Application Audience (AUD) Tag from the Access
 *                       application's Overview tab
 *
 * Optional (local dev only, NEVER in production):
 *   AUTH_DEV_BYPASS     if "true", skip JWT verification and use
 *                       AUTH_DEV_EMAIL as the authenticated identity
 *   AUTH_DEV_EMAIL      dev identity when bypass is on
 */
import { jwtVerify, createRemoteJWKSet } from 'jose';

// JWKS cache per isolate — jose also caches internally.
let jwksCache = null;
let jwksTeamDomain = null;

function getJWKS(teamDomain) {
  if (!jwksCache || jwksTeamDomain !== teamDomain) {
    jwksCache = createRemoteJWKSet(
      new URL(`https://${teamDomain}/cdn-cgi/access/certs`)
    );
    jwksTeamDomain = teamDomain;
  }
  return jwksCache;
}

/**
 * Verify the Access JWT and return the user's email.
 * Throws AuthError on any failure.
 */
async function verifyAccessJWT(request, env) {
  const token = request.headers.get('Cf-Access-Jwt-Assertion');
  if (!token) {
    throw new AuthError(401, 'Missing Access token. Sign in via Cloudflare Access.');
  }
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) {
    throw new AuthError(500, 'Server auth is not configured (ACCESS_TEAM_DOMAIN / ACCESS_AUD).');
  }

  const jwks = getJWKS(env.ACCESS_TEAM_DOMAIN);
  try {
    const { payload } = await jwtVerify(token, jwks, {
      issuer: `https://${env.ACCESS_TEAM_DOMAIN}`,
      audience: env.ACCESS_AUD,
    });
    const email = (payload.email || '').toLowerCase().trim();
    if (!email) throw new AuthError(401, 'Access token has no email claim.');
    return email;
  } catch (err) {
    if (err instanceof AuthError) throw err;
    throw new AuthError(401, `Invalid Access token: ${err.message}`);
  }
}

/**
 * Verify the JWT, upsert the user profile, and return it.
 * Returns { email, name, role } — name may be null for brand-new users.
 */
export async function authenticate(request, env) {
  let email;
  if (env.AUTH_DEV_BYPASS === 'true') {
    email = (env.AUTH_DEV_EMAIL || 'dev@localhost').toLowerCase();
  } else {
    email = await verifyAccessJWT(request, env);
  }

  // Upsert the profile row. On first login the row is created with
  // name=NULL; the frontend will prompt for a name.
  await env.DB
    .prepare('INSERT OR IGNORE INTO users (email) VALUES (?)')
    .bind(email)
    .run();

  const row = await env.DB
    .prepare('SELECT email, name, role FROM users WHERE email = ?')
    .bind(email)
    .first();

  return { email: row.email, name: row.name, role: row.role };
}

/**
 * Update the authenticated user's display name.
 */
export async function setUserName(env, email, name) {
  const trimmed = (name || '').trim();
  if (!trimmed) throw new AuthError(400, 'Name cannot be empty.');
  if (trimmed.length > 80) throw new AuthError(400, 'Name is too long (max 80 chars).');

  await env.DB
    .prepare('UPDATE users SET name = ? WHERE email = ?')
    .bind(trimmed, email)
    .run();

  const row = await env.DB
    .prepare('SELECT email, name, role FROM users WHERE email = ?')
    .bind(email)
    .first();
  return { email: row.email, name: row.name, role: row.role };
}

export class AuthError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
    this.name = 'AuthError';
  }
}
