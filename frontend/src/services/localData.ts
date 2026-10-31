// The Data Manager's browser side: reading a chosen folder, posting files to localhost, and writing outputs back.
// The browser never reveals a folder's absolute path, so the backend only ever receives the files themselves.
import { ApiError } from "./api";
import type { LocalUpload } from "./types";

const DATA_FILE = /\.(csv|zip|parquet)$/i;

export interface LocalEntry {
  id: string;
  file: File;
  relativePath: string;
  kind: string;
  selected: boolean;
}

interface DirectoryPickerOptions { mode?: "read" | "readwrite" }
type DirectoryPicker = (options?: DirectoryPickerOptions) => Promise<FileSystemDirectoryHandle>;
type PermissionedHandle = FileSystemDirectoryHandle & {
  queryPermission?: (d: { mode: "readwrite" }) => Promise<PermissionState>;
  requestPermission?: (d: { mode: "readwrite" }) => Promise<PermissionState>;
};

const picker = (): DirectoryPicker | undefined => (window as unknown as { showDirectoryPicker?: DirectoryPicker }).showDirectoryPicker;

export const folderApiAvailable = () => typeof picker() === "function";

const toEntry = (file: File, relativePath: string): LocalEntry => ({
  id: relativePath, file, relativePath, kind: (file.name.split(".").pop() ?? "").toUpperCase(), selected: true,
});

async function walk(dir: FileSystemDirectoryHandle, prefix: string, found: LocalEntry[]) {
  for await (const handle of (dir as unknown as { values(): AsyncIterable<FileSystemHandle> }).values()) {
    if (handle.name.startsWith(".")) continue;
    const path = `${prefix}${handle.name}`;
    if (handle.kind === "directory") await walk(handle as FileSystemDirectoryHandle, `${path}/`, found);
    else if (DATA_FILE.test(handle.name)) found.push(toEntry(await (handle as FileSystemFileHandle).getFile(), path));
  }
}

/** Must run from a real click. Returns null if the person cancels the dialog. */
export async function pickInputFolder(): Promise<{ name: string; entries: LocalEntry[] } | null> {
  try {
    const dir = await picker()!();
    const entries: LocalEntry[] = [];
    await walk(dir, "", entries);
    return { name: dir.name, entries: entries.sort((a, b) => a.relativePath.localeCompare(b.relativePath)) };
  } catch (e) {
    if ((e as DOMException).name === "AbortError") return null;
    throw e;
  }
}

/** For browsers without the File System Access API: the files from an <input webkitdirectory>. */
export function entriesFromInput(files: FileList): { name: string; entries: LocalEntry[] } {
  const all = Array.from(files);
  const path = (f: File) => (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
  const name = path(all[0] ?? new File([], "")).split("/")[0];
  const entries = all
    .filter((f) => DATA_FILE.test(f.name) && !f.name.startsWith("."))
    .map((f) => toEntry(f, path(f).split("/").slice(1).join("/") || f.name));
  return { name, entries: entries.sort((a, b) => a.relativePath.localeCompare(b.relativePath)) };
}

/** Normal multipart upload, with progress the browser actually measured. */
export function uploadFiles(entries: LocalEntry[], datasetType: string, onProgress: (share: number) => void): Promise<LocalUpload> {
  const form = new FormData();
  form.append("dataset_type", datasetType);
  for (const entry of entries) {
    form.append("files", entry.file, entry.file.name);
    form.append("relative_paths", entry.relativePath);
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/local-data/upload");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError("The upload to the local backend failed. Is it running?"));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as LocalUpload);
      else reject(new ApiError(xhr.response?.detail ?? xhr.statusText));
    };
    xhr.send(form);
  });
}

/** Must run from a real click. Returns null if the person cancels the dialog. */
export async function pickOutputFolder(): Promise<FileSystemDirectoryHandle | null> {
  try {
    return await picker()!({ mode: "readwrite" });
  } catch (e) {
    if ((e as DOMException).name === "AbortError") return null;
    throw e;
  }
}

export class ExistingRunFolder extends Error {}

/** A file counts as saved only after the browser finishes the write and the size on disk reads back correctly. */
export async function exportRun(dir: FileSystemDirectoryHandle, runId: string, names: string[], overwrite: boolean,
                                onStatus: (name: string, status: string) => void) {
  const handle = dir as PermissionedHandle;
  if (handle.queryPermission && (await handle.queryPermission({ mode: "readwrite" })) !== "granted") {
    if (!handle.requestPermission || (await handle.requestPermission({ mode: "readwrite" })) !== "granted") {
      throw new ApiError("The browser did not grant permission to write to that folder.");
    }
  }
  let exists = true;
  try {
    await dir.getDirectoryHandle(runId);
  } catch (e) {
    if ((e as DOMException).name !== "NotFoundError") throw e;
    exists = false;
  }
  if (exists && !overwrite) throw new ExistingRunFolder(runId);
  const folder = await dir.getDirectoryHandle(runId, { create: true });
  for (const name of names) {
    onStatus(name, "writing");
    const response = await fetch(`/api/local-data/runs/${runId}/files/${name}`);
    if (!response.ok) throw new ApiError(`${name} could not be read from the backend (${response.status}).`);
    const blob = await response.blob();
    const file = await folder.getFileHandle(name, { create: true });
    const writable = await file.createWritable();
    await writable.write(blob);
    await writable.close();
    const saved = await file.getFile();
    if (saved.size !== blob.size) throw new ApiError(`${name}: wrote ${saved.size} bytes, expected ${blob.size}.`);
    onStatus(name, "saved");
  }
}
