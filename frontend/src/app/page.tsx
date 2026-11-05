import { connection } from "next/server";
import { LoveStone } from "@/components/lovestone";

export default async function Page() {
  await connection();
  return <LoveStone liveEnabled={Boolean(process.env.LOVESTONE_AGENT_API_URL)} />;
}
