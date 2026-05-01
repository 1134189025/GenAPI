import { createReadStream, existsSync, realpathSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, "..", "out");
const host = process.env.HOST || "0.0.0.0";
const port = Number(process.env.PORT || 3000);

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

const staleNextScript = [
  "try{",
  "var key='genapi:stale-next-script-reload';",
  "if(!sessionStorage.getItem(key)){sessionStorage.setItem(key,'1');location.reload();}",
  "else{console.error('Genapi static assets are stale; please hard refresh.');}",
  "}catch(error){location.reload();}",
].join("");

function isInsideDirectory(filePath, directory) {
  const relativePath = relative(directory, filePath);
  return relativePath === "" || (!relativePath.startsWith("..") && !isAbsolute(relativePath));
}

function isExistingFile(filePath) {
  return existsSync(filePath) && statSync(filePath).isFile();
}

function statusPlan(status, message) {
  return { status, message };
}

function isStaleNextScriptRequest(pathname) {
  return pathname.startsWith("/_next/static/") && pathname.endsWith(".js");
}

function staleNextScriptPlan() {
  return {
    status: 200,
    staleNextScript: true,
    body: staleNextScript,
    headers: {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-store",
    },
  };
}

function responseHeadersForFile(filePath) {
  const extension = extname(filePath).toLowerCase();
  const normalizedPath = filePath.replaceAll("\\", "/");
  const headers = {
    "Content-Type": contentTypes[extension] || "application/octet-stream",
  };
  if (extension === ".html") {
    headers["Cache-Control"] = "no-cache, no-store, must-revalidate";
  } else if (normalizedPath.includes("/_next/static/")) {
    headers["Cache-Control"] = "public, max-age=31536000, immutable";
  }
  return headers;
}

export function resolveStaticRequest(url, staticOutDir = outDir) {
  let pathname;
  try {
    const rawUrl = String(url || "/");
    const isAbsoluteUrl = /^[a-z][a-z\d+.-]*:\/\//i.test(rawUrl);
    if (isAbsoluteUrl) {
      new URL(rawUrl);
    }
    const rawPath = isAbsoluteUrl
      ? rawUrl.replace(/^[a-z][a-z\d+.-]*:\/\/[^/?#]*/i, "").split(/[?#]/, 1)[0] || "/"
      : rawUrl.split(/[?#]/, 1)[0] || "/";
    pathname = decodeURIComponent(rawPath);
  } catch {
    return statusPlan(400, "Bad request");
  }

  const root = resolve(staticOutDir);
  const segments = pathname.split(/[\\/]+/).filter(Boolean);
  if (segments.some((segment) => segment === "..")) {
    return statusPlan(403, "Forbidden");
  }

  let filePath = segments.length === 0 ? join(root, "index.html") : join(root, ...segments);
  if (!isInsideDirectory(resolve(filePath), root)) {
    return statusPlan(403, "Forbidden");
  }

  if (existsSync(filePath) && statSync(filePath).isDirectory()) {
    filePath = join(filePath, "index.html");
  }

  if (existsSync(filePath)) {
    if (!statSync(filePath).isFile()) {
      return statusPlan(404, "Not found");
    }
    const realRoot = realpathSync(root);
    const realFile = realpathSync(filePath);
    if (!isInsideDirectory(realFile, realRoot)) {
      return statusPlan(403, "Forbidden");
    }
    return { status: 200, filePath };
  }

  const firstSegment = pathname.split("/", 2)[1] || "";
  const isReservedPath = ["api", "v1", "auth"].includes(firstSegment);
  const hasExtension = Boolean(extname(filePath));
  if (isStaleNextScriptRequest(pathname)) {
    return staleNextScriptPlan();
  }
  if (isReservedPath || hasExtension) {
    return statusPlan(404, "Not found");
  }

  const indexPath = join(root, "index.html");
  if (!isExistingFile(indexPath)) {
    return statusPlan(404, "Not found");
  }
  return { status: 200, filePath: indexPath, fallback: true };
}

function sendFile(response, filePath) {
  response.writeHead(200, responseHeadersForFile(filePath));
  createReadStream(filePath).pipe(response);
}

if (import.meta.main) {
  if (!existsSync(outDir)) {
    console.error(`Static export directory not found: ${outDir}. Run bun run build first.`);
    process.exit(1);
  }

  createServer((request, response) => {
    const plan = resolveStaticRequest(request.url);
    if (plan.status !== 200) {
      response.writeHead(plan.status);
      response.end(plan.message);
      return;
    }
    if (plan.body !== undefined) {
      response.writeHead(plan.status, plan.headers || {});
      response.end(plan.body);
      return;
    }

    sendFile(response, plan.filePath);
  }).listen(port, host, () => {
    console.log(`Serving ${outDir} on http://${host}:${port}`);
  });
}
