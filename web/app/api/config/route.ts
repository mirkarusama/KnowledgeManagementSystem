// Runtime settings for the browser, read from environment variables when the container starts
// (so the same image works on a laptop and in the data centre).
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({
    authority: process.env.NEXT_PUBLIC_OIDC_AUTHORITY ?? "http://localhost:8080/realms/kms",
    clientId: process.env.NEXT_PUBLIC_OIDC_CLIENT_ID ?? "kms-web",
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8088",
  });
}
