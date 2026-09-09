/**
 * unsubscribe
 * Supabase Edge Function — one-click unsubscribe via token.
 * GET /functions/v1/unsubscribe?token=<uuid>
 * Sets email_enabled = false for the matching subscriber.
 */

const SUPABASE_URL         = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const corsHeaders = {
  "Access-Control-Allow-Origin": "https://whatson.movie",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Content-Type": "application/json",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const url   = new URL(req.url);
  const token = url.searchParams.get("token")?.trim();

  if (!token) {
    return new Response(
      JSON.stringify({ error: "Missing token" }),
      { status: 400, headers: corsHeaders }
    );
  }

  // Validate it looks like a UUID (basic guard against injection)
  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuidRe.test(token)) {
    return new Response(
      JSON.stringify({ error: "Invalid token" }),
      { status: 400, headers: corsHeaders }
    );
  }

  // Find the subscriber
  const findRes = await fetch(
    `${SUPABASE_URL}/rest/v1/subscribers?select=email,email_enabled&unsubscribe_token=eq.${token}`,
    {
      headers: {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": `Bearer ${SUPABASE_SERVICE_KEY}`,
      },
    }
  );

  if (!findRes.ok) {
    console.error("Supabase lookup error:", await findRes.text());
    return new Response(
      JSON.stringify({ error: "Database error" }),
      { status: 500, headers: corsHeaders }
    );
  }

  const rows: Array<{ email: string; email_enabled: boolean }> = await findRes.json();

  if (!rows.length) {
    return new Response(
      JSON.stringify({ status: "not_found" }),
      { status: 404, headers: corsHeaders }
    );
  }

  const { email, email_enabled } = rows[0];

  // Already unsubscribed — idempotent, return success
  if (!email_enabled) {
    return new Response(
      JSON.stringify({ status: "already_unsubscribed" }),
      { status: 200, headers: corsHeaders }
    );
  }

  // Set email_enabled = false
  const patchRes = await fetch(
    `${SUPABASE_URL}/rest/v1/subscribers?unsubscribe_token=eq.${token}`,
    {
      method: "PATCH",
      headers: {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": `Bearer ${SUPABASE_SERVICE_KEY}`,
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
      },
      body: JSON.stringify({ email_enabled: false }),
    }
  );

  if (!patchRes.ok) {
    console.error("Supabase patch error:", await patchRes.text());
    return new Response(
      JSON.stringify({ error: "Could not unsubscribe" }),
      { status: 500, headers: corsHeaders }
    );
  }

  console.log(`Unsubscribed: ${email}`);
  return new Response(
    JSON.stringify({ status: "unsubscribed" }),
    { status: 200, headers: corsHeaders }
  );
});
