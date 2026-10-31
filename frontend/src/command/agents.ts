// The five agents' identity. Kept apart from AgentScene, so the panels don't pull three.js into the main bundle.
export type AgentId = "A1" | "A2" | "A3" | "A4" | "A5";
export const AGENT_IDS: AgentId[] = ["A1", "A2", "A3", "A4", "A5"];

export const AGENT_NAMES: Record<AgentId, string> = {
  A1: "Threat Detection", A2: "Anomaly Detection", A3: "Evidence Fusion", A4: "Temporal Correlation", A5: "Adaptive Defense",
};

export const AGENT_LOOK: Record<AgentId, { color: number; css: string; position: [number, number]; side: "left" | "right" }> = {
  A1: { color: 0x2f8bff, css: "#4da3ff", position: [-3.5, -1.15], side: "right" },
  A2: { color: 0x19d6a8, css: "#2ee6b8", position: [3.5, -1.15], side: "left" },
  A3: { color: 0xa04dff, css: "#b57bff", position: [-3.35, 2.05], side: "right" },
  A4: { color: 0xff9a1f, css: "#ffb04d", position: [0, 3.2], side: "right" },
  A5: { color: 0xff2d43, css: "#ff5c6c", position: [3.35, 2.05], side: "left" },
};
