// agent-handoff — оркестратор передаёт разговор project-агенту и забирает обратно.
// Поверх openclaw SessionBindingService / persisted conversation-binding store.
// Доказано в R3 (binding-proof.mjs): createConversationBindingRecord/resolve/unbind
// round-trip'ят на реальном store ~/.openclaw-dev/bindings/current-conversations.json
// (кросс-процессно → переживают рестарт gateway). resolveByConversation = то, что читает inbound-роутер.
//
// СТАТУС: артефакт R4-ready. На полигоне (без канала) tools/hook нечем дёрнуть инбаунд —
// полная боевая проверка на telegram (R4). Шейпы, помеченные TODO(verify@install), уточнить
// при `openclaw --dev plugins install <path> --link` (ошибки покажут точные сигнатуры).

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import {
  createConversationBindingRecord,
  resolveConversationBindingRecord,
  unbindConversationBindingRecord,
} from "openclaw/plugin-sdk/conversation-runtime";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { homedir } from "node:os";
import { readFileSync } from "node:fs";
import { Type } from "@sinclair/typebox";

const pexec = promisify(execFile);
// Движки (R5/R6): scaffold + портфельные команды. Тонкие обёртки шеллят сюда.
const SPAWN_SCRIPT = `${homedir()}/secretary/spawn/create_project.py`;
const PROJECT_CMD = `${homedir()}/secretary/spawn/project_cmd.py`;
const CRM_SYNC = `${homedir()}/secretary/spawn/crm_sync.py`;
const OPENCLAW_BIN = `${homedir()}/.nvm/versions/node/v24.14.1/bin/openclaw`;

// Общий раннер python-движка: возвращает распарсенный JSON (движки печатают JSON в stdout).
async function runPy(script: string, args: string[]): Promise<any> {
  const { stdout } = await pexec("python3.13", [script, ...args], { timeout: 60000 });
  return JSON.parse(stdout);
}
// Профиль определяем по state-dir процесса gateway: dev (полигон) vs боевой.
const detectProfile = (): "dev" | "prod" =>
  (process.env.OPENCLAW_STATE_DIR ?? "").includes("openclaw-dev") ? "dev" : "prod";

// Стоп-слова → гарантированный возврат к основному уму.
// Явные command-like фразы — НЕ срабатывать на обычной речи владельца ("хватит" и пр. убраны).
const STOP_WORDS = ["/return", "/unfocus", "верни управление", "stop handoff"];

// session-key цели: прямые DM коллапсируют в agent:<id>:<mainKey> (mainKey=main по умолчанию).
const sessionKeyFor = (agentId: string) => `agent:${agentId}:main`;

// Single-user secretary: the conversation key = the owner's telegram DM chat id, read from
// config.json (gitignored; template config.example.json). CRITICAL: the telegram inbound router
// resolves the binding by chat id (resolveByConversation → getByConversationId(ref.conversationId)).
// Verified by hand: a binding under the chat id re-rolls, under the session UUID it does NOT — so
// do NOT fall back to ctx.sessionId (UUID); that was the handoff bug.
function ownerChat(): string {
  try {
    return String(JSON.parse(readFileSync(`${homedir()}/secretary/config.json`, "utf8")).telegram_chat_id ?? "");
  } catch {
    return "";
  }
}
const OWNER_TELEGRAM_CHAT = ownerChat();

// ConversationRef for bind/resolve/unbind. FIXED key: the only secretary conversation = the
// owner's telegram DM — what the inbound router sees. ctx.deliveryContext
// часто пуст, а у разных агентов канал/sessionId в ctx расходятся → handoff_to_agent (bind) и
// return_to_main (resolve+unbind) промахивались мимо друг друга, и возврат не находил привязку
// (очищалась лишь по idle-таймауту). Единый ключ для ОБЕИХ сторон лечит и заход, и возврат.
function refFromToolCtx(_ctx: any) {
  return { channel: "telegram", accountId: "default", conversationId: OWNER_TELEGRAM_CHAT };
}

export default definePluginEntry({
  id: "agent-handoff",
  name: "Agent Handoff",
  description: "Оркестратор передаёт разговор project-агенту и забирает обратно (поверх SessionBindingService).",
  register(api) {
    // 1) tool: основной ум передаёт текущий разговор project-агенту.
    // ctx-зависимая → ФАБРИКА (ctx)=>tool: ref берём из доверенного ctx (deliveryContext), не из args.
    api.registerTool((ctx: any) => ({
      name: "handoff_to_agent",
      label: "Передать разговор агенту",
      description:
        "Передать текущий разговор указанному project-агенту — он станет отвечать в этом чате. " +
        "Возврат: tool return_to_main, либо стоп-слово пользователя.",
      parameters: Type.Object({
        agentId: Type.String({ description: "id целевого project-агента, напр. aidrc / typst-studio" }),
      }),
      async execute(_toolCallId: string, params: { agentId: string }, _signal?: AbortSignal) {
        const ref = refFromToolCtx(ctx);
        const rec = await createConversationBindingRecord({
          targetSessionKey: sessionKeyFor(params.agentId),
          targetKind: "session",
          conversation: ref,
        });
        // Детерминированное приветствие: пинаем агента поздороваться СРАЗУ (fire-and-forget),
        // не на «авось вспомнит при следующем сообщении» — реактивно он не опознаёт «первый ход».
        const greet = `[служебное, не показывай дословно: pluttan только что переключился на тебя через хэнд-офф — это самое начало разговора с ним прямо сейчас. Поздоровайся и коротко представься как ум проекта (1-2 фразы), поставь свою готик-шапку как обычно. Затем начни онбординг.]`;
        pexec(OPENCLAW_BIN, ["agent", "--agent", params.agentId, "--to", OWNER_TELEGRAM_CHAT,
          "--channel", "telegram", "--deliver", "--message", greet], { timeout: 120000 }).catch(() => {});
        return { content: [{ type: "text", text: `handoff → ${params.agentId} (binding ${rec.bindingId})` }],
          details: { agentId: params.agentId, bindingId: rec.bindingId } };
      },
    }) as any);

    // 2) tool: вернуть управление основному уму (ctx-зависимая → фабрика)
    api.registerTool((ctx: any) => ({
      name: "return_to_main",
      label: "Вернуть управление",
      description: "Вернуть управление разговором основному уму (снять активный hand-off).",
      parameters: Type.Object({}),
      async execute(_toolCallId: string, _p: unknown, _signal?: AbortSignal) {
        const ref = refFromToolCtx(ctx);
        const cur = resolveConversationBindingRecord(ref);
        if (!cur) return { content: [{ type: "text", text: "return: активного hand-off нет" }], details: { removed: 0 } };
        const removed = await unbindConversationBindingRecord({ targetSessionKey: cur.targetSessionKey, reason: "return_to_main" });
        return { content: [{ type: "text", text: `return ← основной ум (снято ${removed.length})` }], details: { removed: removed.length } };
      },
    }) as any);

    // 3) hook: стоп-слово → авто-возврат (гарантия возврата)
    // TODO(verify@install): точное имя события и shape (event.text / event.message.text / event.conversation)
    api.registerHook("message_received", async (event: any) => {
      const text = String(event?.text ?? event?.message?.text ?? "").toLowerCase();
      if (!STOP_WORDS.some((w) => text.includes(w))) return;
      const ref = {
        channel: event?.channel ?? event?.messageChannel ?? "telegram",
        accountId: event?.accountId ?? event?.agentAccountId ?? "default",
        conversationId: event?.conversationId ?? event?.sessionId ?? "unknown",
      };
      const cur = resolveConversationBindingRecord(ref);
      if (cur) await unbindConversationBindingRecord({ targetSessionKey: cur.targetSessionKey, reason: "stop-word" });
    }, { name: "agent-handoff-stopword" });

    // 4) ops/proof HTTP route (in-gateway). auth="gateway"; handler — сырой (IncomingMessage, ServerResponse).
    try {
      api.registerHttpRoute({
        path: "/handoff",
        auth: "gateway",
        async handler(req, res) {
          const chunks: Buffer[] = [];
          for await (const c of req) chunks.push(c as Buffer);
          let body: any = {};
          try { body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch { /* keep {} */ }
          const ref = {
            channel: body.channel ?? "telegram",
            accountId: body.accountId ?? "default",
            conversationId: body.conversationId ?? "ops",
          };
          let result: unknown;
          if (body.action === "bind") {
            result = await createConversationBindingRecord({ targetSessionKey: sessionKeyFor(body.agentId), targetKind: "session", conversation: ref });
          } else if (body.action === "resolve") {
            result = resolveConversationBindingRecord(ref);
          } else if (body.action === "unbind") {
            const cur = resolveConversationBindingRecord(ref);
            result = cur ? await unbindConversationBindingRecord({ targetSessionKey: cur.targetSessionKey, reason: "ops" }) : [];
          } else {
            result = { error: "unknown action (bind|resolve|unbind)" };
          }
          res.writeHead(200, { "content-type": "application/json" });
          res.end(JSON.stringify({ ok: true, action: body.action, result }));
          return true;
        },
      });
    } catch (e: any) {
      api.logger?.warn?.(`agent-handoff: http route skipped (${e?.message ?? e})`);
    }

    // 5) tool: завести НОВЫЙ project-агент по команде (R5 спавн-по-команде).
    // Шеллит в движок create_project.py (scaffold из шаблона + agentDir + репо + Twenty-Track + agents.list).
    // Активация требует рестарта gateway (hot-reload в openclaw нет): полигон — свободно;
    // боевой — НЕ авто-рестартим (M0 священен), возвращаем команду для окна.
    api.registerTool({
      name: "create_project",
      label: "Завести project-агента",
      description:
        "Завести НОВЫЙ выделенный ум проекта по имени: scaffold (репо+папка+Track-оболочка в Twenty+workspace из шаблона+agentDir) и запись в agents.list. " +
        "Агент сам онбордится (расспрашивает подноготную+монетизацию) при первом контакте. " +
        "ВАЖНО: для активации нужен рестарт gateway — на боевом выполняется ОТДЕЛЬНО в окно без M0-пинков.",
      parameters: Type.Object({
        name: Type.String({ description: "Человекочитаемое имя проекта (slug нормализуется автоматически)" }),
        repoPath: Type.Optional(Type.String({ description: "необяз. путь репо (default ~/pr/pets/<slug>)" })),
      }),
      async execute(_toolCallId: string, p: { name: string; repoPath?: string }, _signal?: AbortSignal) {
        const profile = detectProfile();
        const args = [SPAWN_SCRIPT, "--profile", profile, "--name", p.name];
        if (p.repoPath) args.push("--repo-path", p.repoPath);
        try {
          const { stdout } = await pexec("python3.13", args, { timeout: 60000 });
          const r = JSON.parse(stdout);
          const note = profile === "dev"
            ? `Активация: \`${r.restart_cmd}\``
            : `Активация на БОЕВОМ — рестарт ВРУЧНУЮ в окно без M0-пинков: \`${r.restart_cmd}\` (бэкап конфига: ${r.config?.backup ?? "—"}).`;
          return { content: [{ type: "text", text:
            `project "${r.name}" (id ${r.slug}) заведён [${profile}]. workspace=${r.workspace}; agents.list += ${r.slug}; Twenty=${r.twenty?.status}; репо=${r.repo?.status}. ${note}` }],
            details: r };
        } catch (e: any) {
          return { content: [{ type: "text", text: `create_project FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } };
        }
      },
    } as any);

    // 6) R6 портфельные тулзы (wip-gate + стадии + приоритайзер). Тонкие обёртки над project_cmd.py.
    // ПРАВИЛЬНЫЙ ШЕЙП AgentTool (pi-agent-core): label + parameters=typebox TSchema +
    // execute(toolCallId, params, signal?, onUpdate?) + результат {content, details}.
    // (Прежний шейп без label / JSON-литерал-параметры → тулза НЕ попадала в набор агента.)
    api.registerTool({
      name: "wip_status",
      label: "WIP статус портфеля",
      description: "Портфель проектов: стадии (BACKLOG/ACTIVE/FROZEN/SHIPPED/KILLED) + WIP-вердикт (сколько активных, есть ли слот).",
      parameters: Type.Object({}),
      async execute(_toolCallId: string, _params: unknown, _signal?: AbortSignal) {
        try {
          const r = await runPy(PROJECT_CMD, ["status"]);
          const text = `портфель: ${JSON.stringify(r.by_stage)}; WIP ${r.wip.active_count}/${r.wip.limit} активных ${JSON.stringify(r.wip.active)} — ${r.wip.available ? "есть слот" : "ЛИМИТ, заморозь что-то"}.`;
          return { content: [{ type: "text", text }], details: r };
        } catch (e: any) {
          return { content: [{ type: "text", text: `wip_status FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } };
        }
      },
    } as any);
    api.registerTool({
      name: "set_project_stage",
      label: "Сменить стадию проекта",
      description: "Сменить стадию проекта (BACKLOG/ACTIVE/FROZEN/SHIPPED/KILLED). Активация (ACTIVE) ГЕЙТИТСЯ WIP-лимитом: если активных уже на лимите — откажет, пока не заморозишь что-то.",
      parameters: Type.Object({
        name: Type.String(),
        stage: Type.Union([Type.Literal("BACKLOG"), Type.Literal("ACTIVE"), Type.Literal("FROZEN"), Type.Literal("SHIPPED"), Type.Literal("KILLED")]),
      }),
      async execute(_toolCallId: string, p: { name: string; stage: string }, _signal?: AbortSignal) {
        try { const r = await runPy(PROJECT_CMD, ["stage", p.name, p.stage]);
          return { content: [{ type: "text", text: r.ok ? `${p.name} → ${r.stage}` : `ОТКАЗ (WIP): ${r.hint}` }], details: r };
        } catch (e: any) { return { content: [{ type: "text", text: `set_project_stage FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } }; }
      },
    } as any);
    api.registerTool({
      name: "set_project_money",
      label: "Денежная цель проекта",
      description: "Задать денежную цель проекта (money_target). Валюта по умолчанию RUB.",
      parameters: Type.Object({
        name: Type.String(),
        amount: Type.Number(),
        currency: Type.Optional(Type.String()),
      }),
      async execute(_toolCallId: string, p: { name: string; amount: number; currency?: string }, _signal?: AbortSignal) {
        const args = ["money", p.name, String(p.amount)];
        if (p.currency) args.push("--currency", p.currency);
        try { const r = await runPy(PROJECT_CMD, args);
          return { content: [{ type: "text", text: `${p.name}: цель ${r.money_target}` }], details: r };
        } catch (e: any) { return { content: [{ type: "text", text: `set_project_money FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } }; }
      },
    } as any);
    api.registerTool({
      name: "prioritize",
      label: "Приоритайзер фокуса",
      description: "Из активных проектов посоветовать, на чём фокус (совет, не приказ; источник истины по приоритетам — STATE '## now').",
      parameters: Type.Object({}),
      async execute(_toolCallId: string, _params: unknown, _signal?: AbortSignal) {
        try { const r = await runPy(PROJECT_CMD, ["prioritize"]);
          return { content: [{ type: "text", text: r.focus ? `фокус: ${r.focus} (из ${r.active_count} активных: ${r.order.map((o: any) => o.name).join(", ")})` : "активных нет" }], details: r };
        } catch (e: any) { return { content: [{ type: "text", text: `prioritize FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } }; }
      },
    } as any);

    // 7) shared-тулза CRM↔Bot (R7): ОДИН вызов атомарно пишет Twenty Track + наг-лист бота (## now).
    // ACTIVE → проект в ## now; FROZEN/SHIPPED/KILLED/BACKLOG → убрать. Откат Twenty при сбое патча STATE.
    api.registerTool({
      name: "crm_bot_sync",
      label: "Синк CRM↔Bot",
      description: "Атомарно обновить проект в ОБОИХ сторах одним вызовом: Twenty Track (стадия/деньги) И наг-лист бота (## now в STATE секретаря). ACTIVE → проект попадает в ## now; FROZEN/SHIPPED/KILLED → убирается. Держит CRM и бота в синхроне.",
      parameters: Type.Object({
        project: Type.String(),
        stage: Type.Optional(Type.Union([Type.Literal("BACKLOG"), Type.Literal("ACTIVE"), Type.Literal("FROZEN"), Type.Literal("SHIPPED"), Type.Literal("KILLED")])),
        money: Type.Optional(Type.Number()),
        currency: Type.Optional(Type.String()),
        note: Type.Optional(Type.String()),
      }),
      async execute(_toolCallId: string, p: { project: string; stage?: string; money?: number; currency?: string; note?: string }, _signal?: AbortSignal) {
        const args = ["--project", p.project];
        if (p.stage) args.push("--stage", p.stage);
        if (typeof p.money === "number") args.push("--money", String(p.money));
        if (p.currency) args.push("--currency", p.currency);
        if (p.note) args.push("--note", p.note);
        try {
          const r = await runPy(CRM_SYNC, args);
          if (!r.ok) return { content: [{ type: "text", text: `crm_bot_sync рассинхрон: ${r.error}; ${r.rolled_back ?? "отката не было"}` }], details: r };
          const bot = r.bot?.changed ? `## now → ${JSON.stringify(r.bot.now_after)}` : "## now без изменений";
          return { content: [{ type: "text", text: `sync ${p.project}: CRM ${JSON.stringify(r.crm)}; ${bot}` }], details: r };
        } catch (e: any) {
          return { content: [{ type: "text", text: `crm_bot_sync FAILED: ${e?.stderr ?? e?.message ?? e}` }], details: { error: String(e) } };
        }
      },
    } as any);

    api.logger?.info?.("agent-handoff registered: handoff_to_agent / return_to_main / create_project / wip_status / set_project_stage / set_project_money / prioritize / crm_bot_sync / stop-word hook / /handoff");
  },
});
