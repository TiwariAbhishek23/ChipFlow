"use client";

import { type ComponentType, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BrainCircuit,
  CheckCircle2,
  Copy,
  Cpu,
  Database,
  Download,
  FileCode2,
  FlaskConical,
  GitBranch,
  Loader2,
  Moon,
  Network,
  Play,
  Send,
  ShieldCheck,
  Sun,
  XCircle
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const DEFAULT_SPEC =
  "Design a 4-bit synchronous up-counter with active-high enable, asynchronous active-low reset, and an overflow flag. Keep the design under 40 standard cells if possible.";

type AgentEvent = {
  run_id: string;
  step: number;
  iteration: number;
  role: string;
  kind: string;
  title: string;
  summary: string;
  payload: Record<string, unknown>;
};

type KGNode = {
  id: string;
  type: string;
  label: string;
  data: Record<string, unknown>;
};

type KGEdge = {
  source: string;
  target: string;
  type: string;
  label?: string;
};

type Snapshot = {
  run_id: string;
  status: "created" | "running" | "passed" | "failed";
  specification: string;
  iteration: number;
  step: number;
  artifacts: {
    module_name: string;
    verilog: string;
    testbench: string;
    synthesis_script: string;
  };
  simulation?: {
    passed: boolean;
    tool: string;
    failure_reason?: string | null;
    stdout: string;
    assertions: string[];
  } | null;
  synthesis?: {
    passed: boolean;
    tool: string;
    cell_count?: number | null;
    estimated_area?: number | null;
    cells: Record<string, number>;
    warning_count: number;
    summary: string;
  } | null;
  knowledge_graph: {
    nodes: KGNode[];
    edges: KGEdge[];
  };
  events: AgentEvent[];
  failures: Array<{ id: string; summary: string; details: string; iteration: number }>;
  fixes: Array<{ id: string; summary: string; details: string; iteration: number }>;
  ai_runtime: AIRuntimeStatus;
};

type AIRuntimeStatus = {
  provider: string;
  model?: string | null;
  sdk: string;
  enabled: boolean;
  reason: string;
};

type KGBackendStatus = {
  backend: string;
  requested: boolean;
  available: boolean;
  uri?: string | null;
  database: string;
  message: string;
  networkx_nodes: number;
  networkx_edges: number;
};

export function ChipFlowApp() {
  const [specification, setSpecification] = useState(DEFAULT_SPEC);
  const [submittedSpec, setSubmittedSpec] = useState(DEFAULT_SPEC);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codeTab, setCodeTab] = useState<"verilog" | "testbench">("verilog");
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  const [runtimeStatus, setRuntimeStatus] = useState<AIRuntimeStatus | null>(null);
  const [graphStatus, setGraphStatus] = useState<KGBackendStatus | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(`${API_BASE}/api/model`, { cache: "no-store" }).then((response) => (response.ok ? response.json() : null)),
      fetch(`${API_BASE}/api/knowledge/status`, { cache: "no-store" }).then((response) => (response.ok ? response.json() : null))
    ])
      .then(([runtime, kg]: [AIRuntimeStatus | null, KGBackendStatus | null]) => {
        if (cancelled) return;
        if (runtime) setRuntimeStatus(runtime);
        if (kg) setGraphStatus(kg);
      })
      .catch(() => {
        if (!cancelled) {
          setRuntimeStatus({
            provider: "unknown",
            model: null,
            sdk: "unknown",
            enabled: false,
            reason: "Backend model status is unavailable."
          });
          setGraphStatus({
            backend: "unknown",
            requested: false,
            available: false,
            uri: null,
            database: "unknown",
            message: "Knowledge graph backend status is unavailable.",
            networkx_nodes: 0,
            networkx_edges: 0
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const latestEvents = snapshot?.events ?? [];
  const latestEvent = latestEvents.at(-1);
  const verilog = snapshot?.artifacts.verilog ?? "";
  const testbench = snapshot?.artifacts.testbench ?? "";
  const activeRuntime = snapshot?.ai_runtime ?? runtimeStatus;

  async function runAgent() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setError(null);
    setSnapshot(null);
    setSubmittedSpec(specification);

    try {
      const response = await fetch(`${API_BASE}/api/runs/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ specification, max_iterations: 12, seed_demo_bug: true }),
        signal: controller.signal
      });
      if (!response.ok || !response.body) {
        throw new Error(`API returned ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const parsed = parseSseFrame(frame);
          if (!parsed) continue;
          if (parsed.event === "snapshot" || parsed.event === "done") {
            const nextSnapshot = parsed.data as Snapshot;
            setSnapshot(nextSnapshot);
            setGraphStatus((previous) =>
              previous
                ? {
                    ...previous,
                    networkx_nodes: nextSnapshot.knowledge_graph.nodes.length,
                    networkx_edges: nextSnapshot.knowledge_graph.edges.length
                  }
                : previous
            );
          }
          if (parsed.event === "done") {
            setRunning(false);
          }
          if (parsed.event === "error") {
            const message = typeof parsed.data.error === "string" ? parsed.data.error : "Unknown stream error";
            setError(message);
            setRunning(false);
          }
        }
      }
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError((caught as Error).message);
      }
    } finally {
      setRunning(false);
    }
  }

  async function exportRun() {
    if (!snapshot) return;
    const response = await fetch(`${API_BASE}/api/runs/${snapshot.run_id}/export`);
    if (!response.ok) {
      setError(`Export failed with ${response.status}`);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `chipflow-${snapshot.run_id}.zip`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function copyCode() {
    const text = codeTab === "verilog" ? verilog : testbench;
    if (text) void navigator.clipboard.writeText(text);
  }

  return (
    <main className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b bg-background/95 px-4 backdrop-blur sm:px-6">
        <div className="flex min-h-16 items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Cpu className="h-5 w-5" aria-hidden />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">ChipFlow Agent</h1>
            <p className="truncate text-xs text-muted-foreground">
              {formatRuntime(activeRuntime)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <GraphBackendPill status={graphStatus} />
          <StatusPill status={snapshot?.status ?? (running ? "running" : "created")} />
          <Button
            variant="outline"
            size="icon"
            onClick={() => setTheme((value) => (value === "dark" ? "light" : "dark"))}
            title="Toggle theme"
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
          <Button variant="outline" onClick={exportRun} disabled={!snapshot}>
            <Download className="mr-2 h-4 w-4" />
            Export
          </Button>
        </div>
        </div>
      </header>

      <section className="border-b bg-muted/20 px-4 py-4 sm:px-6">
        <div className="grid gap-3 md:grid-cols-4">
          <ProofPoint icon={BrainCircuit} label="Long-horizon loop" value={`${snapshot?.step ?? 0} graph steps`} />
          <ProofPoint icon={Database} label="Knowledge graph" value={graphStatusLabel(graphStatus)} />
          <ProofPoint icon={ShieldCheck} label="Quality gates" value={qualityGateLabel(snapshot)} />
          <ProofPoint icon={GitBranch} label="Self-correction" value={`${snapshot?.failures.length ?? 0} failures / ${snapshot?.fixes.length ?? 0} fixes`} />
        </div>
      </section>

      <div className="grid gap-4 p-4 xl:grid-cols-[minmax(300px,0.9fr)_minmax(500px,1.35fr)_minmax(360px,1fr)] lg:p-6">
        <section className="panel flex min-h-[620px] flex-col">
          <div className="panel-header">
            <div className="flex items-center gap-2">
              <Play className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Run Console</h2>
            </div>
            {running ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : null}
          </div>
          <div className="flex flex-1 flex-col gap-4 p-4">
            <div className="rounded-lg border bg-muted/35 p-3 text-sm leading-6">{submittedSpec}</div>
            <textarea
              className="min-h-36 resize-none rounded-md border bg-background p-3 text-sm leading-6 outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
              value={specification}
              onChange={(event) => setSpecification(event.target.value)}
              spellCheck={false}
            />
            <Button onClick={runAgent} disabled={running || !specification.trim()}>
              {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
              Run Agents
            </Button>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <MiniCheck label="Seeded bug" active />
              <MiniCheck label="Simulation repair" active={Boolean(snapshot?.fixes.length)} />
              <MiniCheck label="Yosys gate" active={Boolean(snapshot?.synthesis)} />
              <MiniCheck label="Graph export" active={Boolean(snapshot)} />
            </div>
            {error ? (
              <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            ) : null}
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border bg-background">
              {latestEvents.length === 0 ? (
                <div className="p-4 text-sm text-muted-foreground">No trajectory yet</div>
              ) : (
                <ol className="divide-y">
                  {latestEvents.map((event) => (
                    <li key={`${event.step}-${event.title}`} className="grid grid-cols-[2rem_1fr] gap-3 p-3">
                      <div className="flex h-8 w-8 items-center justify-center rounded-md border bg-muted/45 text-xs font-semibold">
                        {event.step}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{event.title}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{event.summary}</p>
                        </div>
                        <span className="shrink-0 rounded-sm bg-secondary px-2 py-1 text-[11px] text-secondary-foreground">
                          {formatRole(event.role)}
                        </span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        </section>

        <section className="panel min-h-[620px]">
          <div className="panel-header">
            <div className="flex items-center gap-2">
              <FileCode2 className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Artifacts</h2>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-md border p-1">
                <button
                  className={cn(
                    "rounded-sm px-3 py-1.5 text-xs font-medium",
                    codeTab === "verilog" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
                  )}
                  onClick={() => setCodeTab("verilog")}
                >
                  Verilog
                </button>
                <button
                  className={cn(
                    "rounded-sm px-3 py-1.5 text-xs font-medium",
                    codeTab === "testbench" ? "bg-primary text-primary-foreground" : "text-muted-foreground"
                  )}
                  onClick={() => setCodeTab("testbench")}
                >
                  Testbench
                </button>
              </div>
              <Button variant="outline" size="icon" onClick={copyCode} disabled={!verilog && !testbench} title="Copy code">
                <Copy className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="panel-body">
            <pre className="mono-scroll h-[520px] whitespace-pre text-foreground">
              {codeTab === "verilog" ? verilog || "RTL will appear here" : testbench || "Testbench will appear here"}
            </pre>
          </div>
        </section>

        <aside className="grid gap-4">
          <SynthesisPanel snapshot={snapshot} />
          <GraphPanel snapshot={snapshot} status={graphStatus} />
          <TrajectoryPanel latestEvent={latestEvent} snapshot={snapshot} runtime={activeRuntime} status={graphStatus} />
        </aside>
      </div>
    </main>
  );
}

function StatusPill({ status }: { status: Snapshot["status"] }) {
  const Icon = status === "passed" ? CheckCircle2 : status === "failed" ? XCircle : status === "running" ? Loader2 : Activity;
  return (
    <span
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-md border px-3 text-xs font-medium capitalize",
        status === "passed" && "border-primary/40 bg-primary/10 text-primary",
        status === "failed" && "border-destructive/40 bg-destructive/10 text-destructive",
        status === "running" && "border-accent/50 bg-accent/10 text-accent-foreground",
        status === "created" && "text-muted-foreground"
      )}
    >
      <Icon className={cn("h-4 w-4", status === "running" && "animate-spin")} />
      {status}
    </span>
  );
}

function GraphBackendPill({ status }: { status: KGBackendStatus | null }) {
  const connected = status?.backend === "neo4j" && status.available;
  return (
    <span
      className={cn(
        "hidden h-9 items-center gap-2 rounded-md border px-3 text-xs font-medium md:inline-flex",
        connected
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-accent/40 bg-accent/10 text-accent-foreground"
      )}
    >
      <Database className="h-4 w-4" />
      {graphStatusLabel(status)}
    </span>
  );
}

function ProofPoint({
  icon: Icon,
  label,
  value
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border bg-card px-4 py-3 shadow-sm">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="truncate text-sm font-semibold">{value}</p>
        </div>
      </div>
    </div>
  );
}

function MiniCheck({ label, active }: { label: string; active: boolean }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-2 py-2",
        active ? "bg-primary/10 text-primary" : "bg-muted/25 text-muted-foreground"
      )}
    >
      <CheckCircle2 className="h-3.5 w-3.5" />
      <span className="truncate">{label}</span>
    </div>
  );
}

function SynthesisPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const synthesis = snapshot?.synthesis;
  const simulation = snapshot?.simulation;
  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Quality Gates</h2>
        </div>
      </div>
      <div className="panel-body grid grid-cols-2 gap-3 text-sm">
        <Metric label="Simulation" value={simulation ? (simulation.passed ? "Pass" : "Fail") : "Pending"} good={simulation?.passed} />
        <Metric label="Synthesis" value={synthesis ? (synthesis.passed ? "Pass" : "Fail") : "Pending"} good={synthesis?.passed} />
        <Metric label="Cells" value={synthesis?.cell_count ?? "n/a"} />
        <Metric label="Area" value={synthesis?.estimated_area ?? "n/a"} />
        <div className="col-span-2 rounded-md border bg-muted/35 p-3 text-xs text-muted-foreground">
          {synthesis?.summary ?? simulation?.failure_reason ?? "Reports will appear after verification and synthesis."}
        </div>
      </div>
    </section>
  );
}

function Metric({ label, value, good }: { label: string; value: string | number; good?: boolean }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-lg font-semibold", good === true && "text-primary", good === false && "text-destructive")}>
        {value}
      </p>
    </div>
  );
}

function GraphPanel({ snapshot, status }: { snapshot: Snapshot | null; status: KGBackendStatus | null }) {
  const graphNodes = snapshot?.knowledge_graph.nodes;
  const graphEdges = snapshot?.knowledge_graph.edges;
  const nodes = useMemo(() => graphNodes ?? [], [graphNodes]);
  const edges = useMemo(() => graphEdges ?? [], [graphEdges]);
  const visible = useMemo(() => nodes.slice(0, 30), [nodes]);
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const centerX = 180;
    const centerY = 150;
    const radius = 105;
    visible.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(visible.length, 1) - Math.PI / 2;
      map.set(node.id, {
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius
      });
    });
    return map;
  }, [visible]);
  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    nodes.forEach((node) => counts.set(node.type, (counts.get(node.type) ?? 0) + 1));
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [nodes]);

  return (
    <section className="panel">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <Network className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Knowledge Graph</h2>
        </div>
        <span className="text-xs text-muted-foreground">
          {nodes.length} nodes / {edges.length} edges
        </span>
      </div>
      <div className="panel-body space-y-3">
        <div className="flex items-center justify-between rounded-md border bg-muted/25 px-3 py-2 text-xs">
          <span className="text-muted-foreground">Backend</span>
          <span className="font-medium">{graphStatusLabel(status)}</span>
        </div>
        <svg viewBox="0 0 360 300" className="h-[260px] w-full rounded-md border bg-background" role="img">
          <defs>
            <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">
              <path d="M 24 0 L 0 0 0 24" fill="none" stroke="currentColor" strokeOpacity="0.05" strokeWidth="1" />
            </pattern>
          </defs>
          <rect width="360" height="300" fill="url(#grid)" />
          {edges.slice(0, 60).map((edge, index) => {
            const source = positions.get(edge.source);
            const target = positions.get(edge.target);
            if (!source || !target) return null;
            return (
              <line
                key={`${edge.source}-${edge.target}-${index}`}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke="currentColor"
                strokeOpacity="0.22"
                strokeWidth={edge.type === "fixed_by" || edge.type === "violates" ? "2" : "1"}
              />
            );
          })}
          {visible.map((node) => {
            const point = positions.get(node.id);
            if (!point) return null;
            return (
              <g key={node.id} transform={`translate(${point.x} ${point.y})`}>
                <circle r="13" className={nodeFill(node.type)} />
                <circle r="18" fill="none" stroke="currentColor" strokeOpacity="0.12" />
                <text y="29" textAnchor="middle" className="fill-muted-foreground text-[9px]">
                  {shortLabel(node.label)}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="grid grid-cols-2 gap-2 text-xs">
          {typeCounts.length === 0 ? (
            <div className="col-span-2 rounded-md border bg-muted/25 p-2 text-muted-foreground">Graph nodes appear after a run.</div>
          ) : (
            typeCounts.map(([type, count]) => (
              <div key={type} className="flex items-center justify-between rounded-md border bg-muted/25 px-2 py-2">
                <span className="flex items-center gap-2">
                  <span className={cn("h-2.5 w-2.5 rounded-full", nodeDot(type))} />
                  {type}
                </span>
                <span className="font-semibold">{count}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function TrajectoryPanel({
  latestEvent,
  snapshot,
  runtime,
  status
}: {
  latestEvent?: AgentEvent;
  snapshot: Snapshot | null;
  runtime: AIRuntimeStatus | null;
  status: KGBackendStatus | null;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="text-sm font-semibold">Trajectory</h2>
      </div>
      <div className="panel-body space-y-3">
        <div className="rounded-md border bg-muted/35 p-3 text-xs text-muted-foreground">
          {latestEvent ? latestEvent.summary : "Waiting for the first planner decision."}
        </div>
        <div className="rounded-md border bg-background p-3 text-xs">
          <p className="font-medium">AI Runtime</p>
          <p className="mt-1 text-muted-foreground">{formatRuntime(runtime)}</p>
        </div>
        <div className="rounded-md border bg-background p-3 text-xs">
          <p className="font-medium">Graph Persistence</p>
          <p className="mt-1 text-muted-foreground">{status?.message ?? "Waiting for backend status."}</p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="rounded-md border p-2">
            <p className="text-muted-foreground">Steps</p>
            <p className="mt-1 text-base font-semibold">{snapshot?.step ?? 0}</p>
          </div>
          <div className="rounded-md border p-2">
            <p className="text-muted-foreground">Failures</p>
            <p className="mt-1 text-base font-semibold">{snapshot?.failures.length ?? 0}</p>
          </div>
          <div className="rounded-md border p-2">
            <p className="text-muted-foreground">Fixes</p>
            <p className="mt-1 text-base font-semibold">{snapshot?.fixes.length ?? 0}</p>
          </div>
        </div>
      </div>
    </section>
  );
}

function parseSseFrame(frame: string): { event: string; data: Record<string, unknown> } | null {
  const lines = frame.split("\n").map((line) => line.trimEnd());
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLines = lines.filter((line) => line.startsWith("data:"));
  if (!eventLine || dataLines.length === 0) return null;
  const event = eventLine.replace("event:", "").trim();
  const data = dataLines.map((line) => line.replace(/^data:\s?/, "")).join("\n");
  return { event, data: JSON.parse(data) as Record<string, unknown> };
}

function formatRole(role: string) {
  return role.replaceAll("_", " ");
}

function formatRuntime(runtime: AIRuntimeStatus | null) {
  if (!runtime) return "Multi-agent RTL design, verification, synthesis, and knowledge graph";
  if (runtime.provider === "openai_agents" && runtime.enabled) {
    return `OpenAI Agents SDK${runtime.model ? ` / ${runtime.model}` : ""}`;
  }
  if (runtime.provider === "openai_agents") {
    return `OpenAI Agents SDK unavailable: ${runtime.reason}`;
  }
  return "Deterministic local RTL workflow";
}

function graphStatusLabel(status: KGBackendStatus | null) {
  if (!status) return "KG status pending";
  if (status.backend === "neo4j" && status.available) return `Neo4j / ${status.database}`;
  if (status.backend === "neo4j") return "Neo4j fallback";
  if (status.backend === "networkx") return "NetworkX fallback";
  return "KG unavailable";
}

function qualityGateLabel(snapshot: Snapshot | null) {
  if (!snapshot?.simulation) return "waiting for simulation";
  if (!snapshot.simulation.passed) return "simulation failed";
  if (!snapshot.synthesis) return "simulation passed";
  return snapshot.synthesis.passed ? "simulation + synthesis passed" : "synthesis failed";
}

function shortLabel(label: string) {
  return label.length > 14 ? `${label.slice(0, 12)}..` : label;
}

function nodeFill(type: string) {
  if (type === "Failure") return "fill-destructive";
  if (type === "Fix") return "fill-primary";
  if (type === "Module") return "fill-accent";
  if (type === "Port") return "fill-sky-500";
  if (type === "ToolOutput") return "fill-violet-500";
  return "fill-muted-foreground";
}

function nodeDot(type: string) {
  if (type === "Failure") return "bg-destructive";
  if (type === "Fix") return "bg-primary";
  if (type === "Module") return "bg-accent";
  if (type === "Port") return "bg-sky-500";
  if (type === "ToolOutput") return "bg-violet-500";
  if (type === "DesignRule") return "bg-emerald-500";
  return "bg-muted-foreground";
}
