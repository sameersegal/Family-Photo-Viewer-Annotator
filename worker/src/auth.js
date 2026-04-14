/**
 * Cloudflare Access — JWT verification and allow-list check.
 *
 * How it works:
 *   1. Cloudflare Access sits in front of this Worker (configured in the
 *      Zero Trust dashboard). When an authenticated user's browser hits
 *      the Worker, Access injects a signed JWT into the
 *      `Cf-Access-Jwt-Assertion` header.
 *   2. We verify that JWT against Access's public keys (JWKS). This is
 *      important: without verification, a malicious caller could bypass
 *      Access entirely by hitting the Worker URL directly with a forged
 *      header.
 *   3. We then cross-check the email claim against the `allowed_users`
 *      table in D1. This is the app-level gate the family can manage
 *      without touching Cloudflare.
 *
 * Required Worker env vars (set in wrangler.toml):
 *   ACCESS_TEAM_DOMAIN  e.g. "yourteam.cloudflareaccess.com"
 *   ACCESS_AUD          the Application Audience (AUD) Tag from the
 *                       Access application — found in Zero Trust dashboard
 *                       under Access > Applications > (your app) > Overview
 *
 * Optional:
 *   AUTH_DEV_BYPASS     if set to "true" (only in local dev!), skips
 *                       JWT verification and uses AUTH_DEV_EMAIL as the
 *                       authenticated identity. Never enable in production.
 *   AUTH_DEV_EMAIL      dev identity when bypass is on
 */
import { jwtVerify, createRemoteJWKSet } from 'jose';

// JWKS is cached per isolate — jose handles the caching internally.
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
 * Throws on any failure (missing header, bad signature, expired, wrong AUD).
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

  let payload;
  try {
    const result = await jwtVerify(token, jwks, {
      issuer: `https://${env.ACCESS_TEAM_DOMAIN}`,
      audience: env.ACCESS_AUD,
    });
    payload = result.payload;
  } catch (err) {
    throw new AuthError(401, `Invalid Access token: ${err.message}`);
  }

  const email = (payload.email || '').toLowerCase().trim();
  if (!email) {
    throw new AuthError(401, 'Access token has no email claim.');
  }
  return email;
}

/**
 * Verify the JWT and check the email against the allow-list in D1.
 * Returns { email, name, role } or throws AuthError.
 */
export async function authenticate(request, env) {
  // Dev bypass — NEVER enable in production
  if (env.AUTH_DEV_BYPASS === 'true') {
    const email = (env.AUTH_DEV_EMAIL || 'dev@localhost').toLowerCase();
    return await lookupAllowedUser(email, env.DB, { devBypass: true });
  }

  const email = await verifyAccessJWT(request, env);
  return await lookupAllowedUser(email, env.DB, { devBypass: false });
}

async function lookupAllowedUser(email, db, { devBypass }) {
  const row = await db
    .prepare('SELECT email, name, role FROM allowed_users WHERE email = ?')
    .bind(email)
    .first();

  if (!row) {
    if (devBypass) {
      // In dev, auto-create a member row so the dev user can work.
      await db
        .prepare('INSERT OR IGNORE INTO allowed_users (email, name, role) VALUES (?, ?, ?)')
        .bind(email, 'Dev User', 'member')
        .run();
      return { email, name: 'Dev User', role: 'member' };
    }
    throw new AuthError(
      403,
      `${email} is not on the family allow-list. Ask the album owner to add you.`
    );
  }
  return { email: row.email, name: row.name, role: row.role };
}

export class AuthError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
    this.name = 'AuthError';
  }
}
