// handoff.mjs — exec-able передача разговора project-агенту и возврат (R8, ось B).
//
// Оркестратор (Шики) дёргает ЧЕРЕЗ exec — без плагин-LLM-тулз (те до агента не доходят
// в этой версии openclaw, см. STATE). Поверх доказанного R3 conversation-binding:
// createConversationBindingRecord/resolve/unbind на реальном persisted store
// (кросс-процессно → переживает рестарт; resolveByConversation = что читает inbound-роутер).
//
//   node handoff.mjs to <agentId>          → привязать текущий разговор к агенту
//   node handoff.mjs return                → снять привязку (вернуть основному уму)
//   node handoff.mjs status                → показать активную привязку
//
// conversation default = the owner's telegram DM (chat id from config.json). Override:
//   --channel <c> --account <a> --conv <id>
// IMPORTANT (verify in prod): conversationId must match what the inbound router
// computes for the owner DM (per log `lane=session:agent:<id>` / resolveByConversation).
//
// Author: pluttan

const MOD = "/home/pluttan/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/dist/plugin-sdk/conversation-runtime.js";
const m = await import(MOD);
const {
  createConversationBindingRecord,
  resolveConversationBindingRecord,
  unbindConversationBindingRecord,
  listSessionBindingRecords,
} = m;

// owner's telegram chat id from config.json (gitignored; template config.example.json)
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
let OWNER_CHAT = "";
try { OWNER_CHAT = String(JSON.parse(readFileSync(`${homedir()}/secretary/config.json`, "utf8")).telegram_chat_id ?? ""); } catch {}

const argv = process.argv.slice(2);
const action = argv[0];
const positional = argv.filter((a, i) => i > 0 && !a.startsWith("--") && !(argv[i - 1] || "").startsWith("--"));
const getFlag = (name, def) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
};

// Секретарь однопользовательский → дефолт = DM владельца в telegram.
const conv = {
  channel: getFlag("--channel", "telegram"),
  accountId: getFlag("--account", "default"),
  conversationId: getFlag("--conv", OWNER_CHAT),
};
const sessionKeyFor = (agentId) => `agent:${agentId}:main`;
const out = (o) => console.log(JSON.stringify(o));

try {
  if (action === "to") {
    const agentId = positional[0] || getFlag("--agent", null);
    if (!agentId) { out({ ok: false, error: "нужен <agentId>: node handoff.mjs to <agentId>" }); process.exit(2); }
    const rec = await createConversationBindingRecord({
      targetSessionKey: sessionKeyFor(agentId), targetKind: "session", conversation: conv,
    });
    out({ ok: true, action: "to", agentId, bindingId: rec?.bindingId, target: rec?.targetSessionKey, status: rec?.status, conv });
  } else if (action === "return") {
    const cur = resolveConversationBindingRecord(conv);
    if (!cur) { out({ ok: true, action: "return", note: "активной привязки нет", conv }); process.exit(0); }
    const removed = await unbindConversationBindingRecord({ targetSessionKey: cur.targetSessionKey, reason: "return" });
    out({ ok: true, action: "return", was: cur.targetSessionKey, removed: removed.length, conv });
  } else if (action === "status") {
    const cur = resolveConversationBindingRecord(conv);
    out({ ok: true, action: "status", bound: cur ? cur.targetSessionKey : null, bindingId: cur?.bindingId ?? null,
          listForTarget: cur ? listSessionBindingRecords(cur.targetSessionKey).length : 0, conv });
  } else {
    out({ ok: false, error: "usage: handoff.mjs to <agentId> | return | status  [--channel c --account a --conv id]" });
    process.exit(2);
  }
} catch (e) {
  out({ ok: false, error: String(e?.message ?? e) });
  process.exit(1);
}
