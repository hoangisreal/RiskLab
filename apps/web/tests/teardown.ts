import { execFileSync } from "node:child_process";

export default function teardown() {
  const run = process.env.RISKLAB_E2E_RUN_ID;
  if (!run || !/^[a-f0-9-]{36}$/.test(run)) return;
  const containers = execFileSync(
    "docker",
    ["ps", "--quiet", "--filter", `label=risklab.e2e=${run}`],
    { encoding: "utf8" },
  ).trim();
  for (const id of containers.split(/\s+/).filter(Boolean)) {
    execFileSync("docker", ["rm", "--force", id], { stdio: "pipe" });
  }
}
