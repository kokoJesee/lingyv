import { handleSupportRequest } from "@/lib/support-relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const GET = handleSupportRequest;
export const POST = handleSupportRequest;
export const PUT = handleSupportRequest;
export const DELETE = handleSupportRequest;
