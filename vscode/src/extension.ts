import * as vscode from "vscode";
import * as http from "http";
import * as https from "https";

type WorkspaceEventType =
  | "file_opened"
  | "file_closed"
  | "file_saved"
  | "cursor_moved"
  | "selection_changed"
  | "workspace_changed";

type WorkspacePayload = {
  event: WorkspaceEventType;
  file?: string;
  project_name?: string;
  metadata?: Record<string, unknown>;
};

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("BOSS Workspace Bridge");

  const send = (payload: WorkspacePayload): void => {
    void sendWorkspaceEvent(payload, output);
  };

  context.subscriptions.push(
    output,
    vscode.commands.registerCommand("boss.openCommandCenter", async () => {
      const endpoint = getEndpoint();
      await vscode.env.openExternal(vscode.Uri.parse(endpoint));
    }),
    vscode.commands.registerCommand("boss.pushWorkspaceState", async () => {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        send(buildFilePayload("file_opened", editor.document.uri));
        sendSelectionEvent(editor);
      }
      vscode.window.showInformationMessage("Pushed current workspace state to BOSS.");
    }),
    vscode.workspace.onDidOpenTextDocument((document) => {
      if (!isFileDocument(document)) {
        return;
      }
      send(buildFilePayload("file_opened", document.uri));
    }),
    vscode.workspace.onDidSaveTextDocument((document) => {
      if (!getEnableSaveEvents() || !isFileDocument(document)) {
        return;
      }
      send(buildFilePayload("file_saved", document.uri));
    }),
    vscode.workspace.onDidCloseTextDocument((document) => {
      if (!isFileDocument(document)) {
        return;
      }
      send(buildFilePayload("file_closed", document.uri));
    }),
    vscode.workspace.onDidChangeWorkspaceFolders((event) => {
      send({
        event: "workspace_changed",
        metadata: {
          workspace_folders: vscode.workspace.workspaceFolders?.map((folder) => folder.name) || [],
          added: event.added.map((folder) => folder.name),
          removed: event.removed.map((folder) => folder.name)
        }
      });
    }),
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (!editor || !isFileDocument(editor.document)) {
        return;
      }
      send(buildFilePayload("file_opened", editor.document.uri));
      if (getEnableSelectionEvents()) {
        sendSelectionEvent(editor);
      }
    }),
    vscode.window.onDidChangeTextEditorSelection((event) => {
      if (!getEnableSelectionEvents() || !isFileDocument(event.textEditor.document)) {
        return;
      }
      const document = event.textEditor.document;
      const selection = event.selections[0];
      if (!selection) {
        return;
      }
      const filePayload = buildFilePayload("selection_changed", document.uri);
      filePayload.metadata = {
        selection: {
          startLine: selection.start.line + 1,
          startCharacter: selection.start.character + 1,
          endLine: selection.end.line + 1,
          endCharacter: selection.end.character + 1
        }
      };
      send(filePayload);

      const cursorPayload = buildFilePayload("cursor_moved", document.uri);
      cursorPayload.metadata = {
        position: {
          line: selection.active.line + 1,
          character: selection.active.character + 1
        }
      };
      send(cursorPayload);
    })
  );

  const activeEditor = vscode.window.activeTextEditor;
  if (activeEditor && isFileDocument(activeEditor.document)) {
    send(buildFilePayload("file_opened", activeEditor.document.uri));
    if (getEnableSelectionEvents()) {
      sendSelectionEvent(activeEditor);
    }
  }
}

export function deactivate(): void {}

function sendSelectionEvent(editor: vscode.TextEditor): void {
  const selection = editor.selection;
  const selectionPayload = buildFilePayload("selection_changed", editor.document.uri);
  selectionPayload.metadata = {
    selection: {
      startLine: selection.start.line + 1,
      startCharacter: selection.start.character + 1,
      endLine: selection.end.line + 1,
      endCharacter: selection.end.character + 1
    }
  };
  void sendWorkspaceEvent(selectionPayload);

  const cursorPayload = buildFilePayload("cursor_moved", editor.document.uri);
  cursorPayload.metadata = {
    position: {
      line: selection.active.line + 1,
      character: selection.active.character + 1
    }
  };
  void sendWorkspaceEvent(cursorPayload);
}

function buildFilePayload(event: WorkspaceEventType, uri: vscode.Uri): WorkspacePayload {
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
  const file = workspaceFolder
    ? vscode.workspace.asRelativePath(uri, false)
    : uri.fsPath;
  return {
    event,
    file,
    project_name: workspaceFolder?.name
  };
}

function isFileDocument(document: vscode.TextDocument): boolean {
  return document.uri.scheme === "file";
}

function getEndpoint(): string {
  const configured = vscode.workspace.getConfiguration().get<string>("boss.endpoint", "http://127.0.0.1:8080");
  return configured.replace(/\/+$/, "");
}

function getEnableSelectionEvents(): boolean {
  return vscode.workspace.getConfiguration().get<boolean>("boss.enableSelectionEvents", true);
}

function getEnableSaveEvents(): boolean {
  return vscode.workspace.getConfiguration().get<boolean>("boss.enableSaveEvents", true);
}

async function sendWorkspaceEvent(payload: WorkspacePayload, output?: vscode.OutputChannel): Promise<void> {
  const endpoint = `${getEndpoint()}/workspace/events`;
  const url = new URL(endpoint);
  const body = JSON.stringify(payload);
  const transport = url.protocol === "https:" ? https : http;

  await new Promise<void>((resolve) => {
    const request = transport.request(
      {
        hostname: url.hostname,
        port: url.port ? Number(url.port) : url.protocol === "https:" ? 443 : 80,
        path: `${url.pathname}${url.search}`,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body)
        }
      },
      (response) => {
        response.on("data", () => undefined);
        response.on("end", () => {
          if ((response.statusCode || 500) >= 400) {
            output?.appendLine(`BOSS workspace event failed (${response.statusCode}) for ${payload.event}`);
          }
          resolve();
        });
      }
    );

    request.on("error", (error) => {
      output?.appendLine(`BOSS workspace event error: ${error.message}`);
      resolve();
    });
    request.write(body);
    request.end();
  });
}
