import { AccessGate } from "./components/AccessGate";
import { RiskMap } from "./components/RiskMap";

export function App() {
  // InSAR is free but login-required: the gate verifies the Weespas-minted token before
  // the map mounts, and bounces anonymous visitors to the Weespas login (see lib/access.ts).
  return (
    <AccessGate>
      <RiskMap />
    </AccessGate>
  );
}
