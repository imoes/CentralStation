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


// ── commands ─────────────────────────────────────────────────────────────────

// Trigger code-server's NATIVE download (explorer.download) on a resource.
// The native command reads the selected file into a blob and creates an
// <a download> in the workbench document — works in every browser. It acts on
// the current explorer SELECTION, so we revealInExplorer first to select it.
async function nativeDownload(uri) {
    await vscode.commands.executeCommand('revealInExplorer', uri);
    await new Promise(r => setTimeout(r, 150));
    await vscode.commands.executeCommand('explorer.download');
}

async function cmdDownload(uri) {
    if (!uri) {
        vscode.window.showWarningMessage('Werkbank: Bitte eine Datei oder Ordner auswählen.');
        return;
    }
    const fspath = uri.fsPath;
    const filename = path.basename(fspath);
    try {
        const stat = fs.statSync(fspath);
        if (!stat.isDirectory()) {
            await nativeDownload(uri);
            return;
        }

        // Folder → backend zips it into .cs-tmp/<name>.zip, then native download.
        if (!UID) { vscode.window.showErrorMessage('Werkbank: CS_USER_ID not set.'); return; }
        const rel = encodeURIComponent(relPath(fspath));
        let zipPath;
        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: `ZIP wird erstellt: ${filename}…` },
            async () => {
                const res = await httpPost(`${BACKEND}/api/ide/workspace/zip?uid=${UID}&path=${rel}`, Buffer.alloc(0));
                zipPath = JSON.parse(res.toString()).zip_path;
            },
        );
        if (!zipPath) throw new Error('Backend lieferte keinen ZIP-Pfad');

        const zipUri = vscode.Uri.file(path.join(workspaceRoot(), zipPath));
        await nativeDownload(zipUri);

        await new Promise(r => setTimeout(r, 800));
        try {
            const tmpDir = vscode.Uri.file(path.join(workspaceRoot(), '.cs-tmp'));
            await vscode.workspace.fs.delete(tmpDir, { recursive: true, useTrash: false });
        } catch (_) {}
    } catch (e) {
        vscode.window.showErrorMessage(`Werkbank Download: ${e.message}`);
    }
}

async function cmdDownloadFolder(uri) {
    if (!uri) {
        vscode.window.showWarningMessage('Werkbank: Bitte einen Ordner auswählen.');
        return;
    }
    try {
        // Native folder download: showDirectoryPicker() in Chromium+HTTPS.
        await nativeDownload(uri);
    } catch (e) {
        vscode.window.showErrorMessage(`Werkbank Ordner-Download: ${e.message}`);
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
        vscode.commands.registerCommand('cs.download',       cmdDownload),
        vscode.commands.registerCommand('cs.downloadFolder', cmdDownloadFolder),
        vscode.commands.registerCommand('cs.upload',         cmdUpload),
    );
}

function deactivate() {}

module.exports = { activate, deactivate };
