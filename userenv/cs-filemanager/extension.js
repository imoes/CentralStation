// Werkbank File Manager — VS Code extension for code-server
// Pure JavaScript, no build step. Runs in Node.js (extension host), not the browser.
//
// Commands:
//   cs.download — download file (data-URI webview) or folder (ZIP via backend)
//   cs.upload   — upload files from local machine into a workspace folder
//
// Backend communication uses the internal Docker network (http://backend:8000).
// CS_USER_ID env var identifies the user; set by userenv_manager.py at container start.

'use strict';

const vscode = require('vscode');
const fs     = require('fs');
const path   = require('path');
const http   = require('http');

const BACKEND = 'http://backend:8000';
const UID     = process.env.CS_USER_ID || '';

// ── helpers ──────────────────────────────────────────────────────────────────

function workspaceRoot() {
    const folders = vscode.workspace.workspaceFolders;
    return folders && folders.length ? folders[0].uri.fsPath : '';
}

function relPath(absPath) {
    const root = workspaceRoot();
    if (root && absPath.startsWith(root)) {
        return absPath.slice(root.length).replace(/^[\\/]/, '');
    }
    return path.basename(absPath);
}

/** Make an HTTP GET request, return a Buffer of the response body. */
function httpGet(url) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const req = http.get({ host: u.hostname, port: u.port || 80, path: u.pathname + u.search,
            headers: { 'X-CS-UID': UID } }, res => {
            if (res.statusCode !== 200) {
                reject(new Error(`HTTP ${res.statusCode}: ${url}`));
                res.resume();
                return;
            }
            const chunks = [];
            res.on('data', c => chunks.push(c));
            res.on('end', () => resolve(Buffer.concat(chunks)));
        });
        req.on('error', reject);
    });
}

/** Make an HTTP POST request with a Buffer body, return Buffer response. */
function httpPost(url, body) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const opts = {
            host: u.hostname, port: u.port || 80, path: u.pathname + u.search,
            method: 'POST',
            headers: { 'X-CS-UID': UID, 'Content-Type': 'application/octet-stream',
                'Content-Length': body.length },
        };
        const req = http.request(opts, res => {
            const chunks = [];
            res.on('data', c => chunks.push(c));
            res.on('end', () => {
                if (res.statusCode >= 400) {
                    reject(new Error(`HTTP ${res.statusCode}: ${Buffer.concat(chunks).toString()}`));
                } else {
                    resolve(Buffer.concat(chunks));
                }
            });
        });
        req.on('error', reject);
        req.write(body);
        req.end();
    });
}

/** Open a webview panel that auto-triggers a download via a data: URI. */
function openDownloadWebview(filename, bytes) {
    const b64  = bytes.toString('base64');
    const mime = filename.endsWith('.zip') ? 'application/zip' : 'application/octet-stream';
    const panel = vscode.window.createWebviewPanel(
        'csFileManagerDownload', `Download: ${filename}`,
        vscode.ViewColumn.One, { enableScripts: true },
    );
    panel.webview.html = `<!DOCTYPE html><html><body style="background:#1e1e1e;color:#ccc;font-family:sans-serif;padding:20px">
<p>Downloading <b>${filename}</b>…</p>
<a id="dl" href="data:${mime};base64,${b64}" download="${filename}"
   style="color:#7ec8e3">Click here if the download doesn't start</a>
<script>document.getElementById('dl').click();setTimeout(()=>{},2000);</script>
</body></html>`;
    setTimeout(() => { try { panel.dispose(); } catch (_) {} }, 8000);
}

// ── commands ─────────────────────────────────────────────────────────────────

async function cmdDownload(uri) {
    if (!uri) {
        vscode.window.showWarningMessage('Werkbank: Bitte eine Datei oder Ordner auswählen.');
        return;
    }
    const fspath = uri.fsPath;
    const filename = path.basename(fspath);
    try {
        const stat = fs.statSync(fspath);
        if (stat.isDirectory()) {
            // Folder → ZIP via backend
            if (!UID) { vscode.window.showErrorMessage('Werkbank: CS_USER_ID not set.'); return; }
            const rel = encodeURIComponent(relPath(fspath));
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: `Creating ZIP: ${filename}…` },
                async () => {
                    const bytes = await httpGet(`${BACKEND}/api/ide/workspace/download?uid=${UID}&path=${rel}`);
                    openDownloadWebview(filename + '.zip', bytes);
                },
            );
        } else {
            // Single file — read locally, no backend needed
            const bytes = fs.readFileSync(fspath);
            openDownloadWebview(filename, bytes);
        }
    } catch (e) {
        vscode.window.showErrorMessage(`Werkbank Download: ${e.message}`);
    }
}

async function cmdUpload(uri) {
    // target: the folder the user right-clicked (or workspace root)
    let targetPath = workspaceRoot();
    if (uri) {
        try {
            const s = fs.statSync(uri.fsPath);
            targetPath = s.isDirectory() ? uri.fsPath : path.dirname(uri.fsPath);
        } catch (_) {}
    }
    if (!targetPath) { vscode.window.showWarningMessage('Werkbank: Kein Workspace-Ordner geöffnet.'); return; }

    // showOpenDialog — in code-server web mode this opens the browser file picker.
    const selected = await vscode.window.showOpenDialog({
        canSelectMany: true,
        canSelectFiles: true,
        canSelectFolders: false,
        openLabel: 'Hochladen',
    });
    if (!selected || selected.length === 0) return;

    let ok = 0, fail = 0;
    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Hochladen…', cancellable: false },
        async progress => {
            for (const src of selected) {
                const name = path.basename(src.fsPath);
                progress.report({ message: name });
                try {
                    // Read via VS Code FS API (works in web mode with browser-picked files)
                    const bytes = await vscode.workspace.fs.readFile(src);
                    const destUri = vscode.Uri.file(path.join(targetPath, name));
                    await vscode.workspace.fs.writeFile(destUri, bytes);

                    // If it's a zip and user wants extraction, offer it
                    if (name.endsWith('.zip') && UID) {
                        const rel = encodeURIComponent(relPath(targetPath));
                        const extract = await vscode.window.showInformationMessage(
                            `${name} hochgeladen. ZIP entpacken?`,
                            'Ja, entpacken', 'Nein',
                        );
                        if (extract === 'Ja, entpacken') {
                            await httpPost(
                                `${BACKEND}/api/ide/workspace/extract?uid=${UID}&path=${rel}&zipname=${encodeURIComponent(name)}`,
                                Buffer.from(bytes),
                            );
                            // Remove the zip after extraction
                            try { await vscode.workspace.fs.delete(destUri); } catch (_) {}
                        }
                    }
                    ok++;
                } catch (e) {
                    vscode.window.showWarningMessage(`${name}: ${e.message}`);
                    fail++;
                }
            }
        },
    );
    const msg = `${ok} Datei(en) hochgeladen` + (fail ? `, ${fail} fehlgeschlagen` : '');
    vscode.window.showInformationMessage(`Werkbank: ${msg}`);
}

// ── activate / deactivate ─────────────────────────────────────────────────────

function activate(context) {
    context.subscriptions.push(
        vscode.commands.registerCommand('cs.download', cmdDownload),
        vscode.commands.registerCommand('cs.upload',   cmdUpload),
    );
}

function deactivate() {}

module.exports = { activate, deactivate };
