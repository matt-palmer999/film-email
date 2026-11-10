/**
 * send-verification-email
 * Supabase Edge Function — triggered by a database webhook on INSERT
 * to the subscribers table. Sends the verification email via Resend.
 */

const RESEND_API_KEY      = Deno.env.get("RESEND_API_KEY")!;
const SUPABASE_URL        = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const FROM_ADDRESS        = "whatson.movie <noreply@whatson.movie>";
const VERIFY_BASE         = "https://whatson.movie/verify/";

Deno.serve(async (req) => {
  try {
    const payload = await req.json();

    // Only handle INSERT events
    if (payload.type !== "INSERT") {
      return new Response("ignored", { status: 200 });
    }

    const record = payload.record ?? {};
    const { email, verification_token: token, lang, verified, verification_sent_at } = record;

    // Skip if already verified, already sent, no token, or no email
    if (verified || verification_sent_at || !token || !email) {
      return new Response("skipped", { status: 200 });
    }

    const isEs     = lang === "es";
    const verifyUrl = `${VERIFY_BASE}?token=${token}`;

    const subject = isEs
      ? "Confirma tu suscripción a whatson.movie"
      : "Confirm your whatson.movie subscription";

    const html = `<!DOCTYPE html>
<html lang="${isEs ? "es" : "en"}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${subject}</title>
</head>
<body style="margin:0;padding:0;background:#0a0810;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0810;padding:40px 16px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#13101e;border:1px solid #2a1f3d;border-radius:12px;padding:40px 36px;">

        <tr><td style="padding-bottom:28px;">
          <span style="font-size:17px;font-weight:700;color:#f0eae0;letter-spacing:-0.5px;">
            whatson<span style="color:#ffb432;">.movie</span>
          </span>
        </td></tr>

        <tr><td style="font-size:22px;font-weight:700;color:#f0eae0;padding-bottom:14px;line-height:1.3;">
          ${isEs ? "¡Confirma tu email!" : "Confirm your email!"}
        </td></tr>

        <tr><td style="font-size:14px;color:#9b8faa;line-height:1.7;padding-bottom:28px;">
          ${isEs
            ? "Gracias por suscribirte a <strong style='color:#c5b8d8;'>whatson.movie</strong>. Haz clic en el botón de abajo para confirmar tu email y activar tu cuenta."
            : "Thanks for signing up to <strong style='color:#c5b8d8;'>whatson.movie</strong>. Click the button below to confirm your email and activate your account."
          }
        </td></tr>

        <tr><td style="padding-bottom:28px;">
          <a href="${verifyUrl}"
             style="display:inline-block;padding:13px 30px;background:#ffb432;color:#0a0810;font-weight:700;font-size:13px;text-decoration:none;border-radius:8px;letter-spacing:0.5px;">
            ${isEs ? "Verificar mi cuenta →" : "Verify my account →"}
          </a>
        </td></tr>

        <tr><td style="font-size:12px;color:#6a5e7a;line-height:1.6;padding-bottom:20px;">
          ${isEs
            ? "Este enlace caduca en 24 horas. Una vez confirmado, podrás personalizar tu cartelera y recibir el email semanal cada jueves."
            : "This link expires in 24 hours. Once confirmed, you can customise your listings and receive the weekly email every Thursday."
          }
        </td></tr>

        <tr><td style="border-top:1px solid #2a1f3d;padding-top:20px;font-size:11px;color:#4a3f5e;">
          ${isEs
            ? "Si no te has suscrito, puedes ignorar este mensaje."
            : "If you didn't sign up, you can safely ignore this email."
          }
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;

    // Send via Resend
    const resendRes = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from:    FROM_ADDRESS,
        to:      [email],
        subject,
        html,
      }),
    });

    if (!resendRes.ok) {
      const err = await resendRes.text();
      console.error("Resend error:", err);
      return new Response(`Resend error: ${err}`, { status: 500 });
    }

    // Mark verification_sent_at so we don't re-send
    await fetch(
      `${SUPABASE_URL}/rest/v1/subscribers?email=eq.${encodeURIComponent(email)}`,
      {
        method: "PATCH",
        headers: {
          "apikey":        SUPABASE_SERVICE_KEY,
          "Authorization": `Bearer ${SUPABASE_SERVICE_KEY}`,
          "Content-Type":  "application/json",
          "Prefer":        "return=minimal",
        },
        body: JSON.stringify({ verification_sent_at: new Date().toISOString() }),
      }
    );

    console.log(`Verification email sent to ${email}`);
    return new Response("ok", { status: 200 });

  } catch (err) {
    console.error("Unexpected error:", err);
    return new Response(String(err), { status: 500 });
  }
});
