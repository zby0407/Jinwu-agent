export interface ProjectInputBinding {
  thread_id: string;
  base_workspace: string;
  project_root: string;
  project_shared: string;
  workspace: string;
  legacy: boolean;
}

export function readRegisteredProjectInput(options: {
  threadId: string;
  baseWorkspace: string;
  binding: ProjectInputBinding;
  path: string;
}): Promise<{ content: Uint8Array; fileName: string; bytes: number }>;
