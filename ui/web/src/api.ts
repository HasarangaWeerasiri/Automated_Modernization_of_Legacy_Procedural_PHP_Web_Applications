// Types and calls of the group UI server (ui/server/main.py). Every number the UI shows comes from these.

export type Option = { id: string; flag: string; default: boolean; label: string; help: string };
export type ComponentConfig = {
  name: string;
  connected: boolean;
  note?: string;
  options?: Option[];
  apps?: string[];
  serve?: { url: string };
};
export type Config = {
  apps: Record<string, { name: string; note: string }>;
  components: Record<string, ComponentConfig>;
};

export type PageRow = {
  key: string;
  path: string;
  role: string;
  route?: string;
  group?: string;
  component?: string;
  function?: string;
  crash: { error: string; where: string } | null;
  tsc: number | null;
  notGenerated: { reason: string } | null;
  components?: string[];
  c2todos?: number;
  severity?: Record<string, number>;
  stage2?: Record<string, number>;
  files?: string[];
};

export type C2Summary = {
  component: "c2";
  app: string;
  rules: string;
  ok: boolean;
  data: boolean;
  layers: boolean;
  input: string;
  pipeline?: string;
  units?: number;
  crashes?: string[];
  notCompiling?: string[];
  tscRan?: boolean;
  pages?: PageRow[];
  severity?: Record<string, number>;
  notGenerated?: { key: string; path: string; reason: string }[];
  shells?: { name: string; group: string }[];
  components?: number;
  c2todos?: number;
  nextBuild?: boolean;
  build?: string;
  layerTables?: { routes: string; assets: string };
  staticFiles?: number;
  nodeLines?: string;
  seconds?: number;
};

export type Run = {
  id: string;
  component: string;
  app: string;
  options: Record<string, boolean>;
  status: "running" | "ok" | "failed";
  started: number;
  finished: number | null;
  exit_code: number | null;
  summary?: C2Summary | null;
};

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response;
}

export const getJSON = async <T,>(url: string): Promise<T> => (await check(await fetch(url))).json();
export const getText = async (url: string): Promise<string> => (await check(await fetch(url))).text();
export const postJSON = async <T,>(url: string, body?: unknown): Promise<T> =>
  (await check(await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }))).json();

export const runFile = (run: string, path: string) =>
  getText(`/api/runs/${run}/file?path=${encodeURIComponent(path)}`);
export const runJSON = async <T,>(run: string, path: string): Promise<T> => JSON.parse(await runFile(run, path));
export const runSource = (run: string, path: string) =>
  getText(`/api/runs/${run}/source?path=${encodeURIComponent(path)}`);
