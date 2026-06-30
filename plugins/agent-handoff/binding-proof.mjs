// R3 proof: dynamic conversation-binding round-trip against openclaw's REAL persisted store.
// Uses core records fns (the same store the inbound router consumes). No channel needed.
const MOD = '/home/pluttan/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/dist/plugin-sdk/conversation-runtime.js';
const m = await import(MOD);
const {
  createConversationBindingRecord,
  resolveConversationBindingRecord,
  unbindConversationBindingRecord,
  listSessionBindingRecords,
  getConversationBindingCapabilities,
} = m;

const action = process.argv[2] || 'cycle';
const tag = process.argv[3] || 'x';
const conv = { channel: 'telegram', accountId: 'default', conversationId: 'r3proof-' + tag };
const target = 'agent:aidrc:main';
const j = (v) => JSON.stringify(v);

async function bind() {
  const rec = await createConversationBindingRecord({ targetSessionKey: target, targetKind: 'session', conversation: conv });
  console.log('BOUND bindingId=%s target=%s status=%s expiresAt=%s', rec?.bindingId, rec?.targetSessionKey, rec?.status, rec?.expiresAt);
  return rec;
}
function resolve(label) {
  const r = resolveConversationBindingRecord(conv);
  console.log('%s resolveByConversation -> %s', label, r ? r.targetSessionKey + ' (binding ' + r.bindingId + ')' : 'null');
  return r;
}
async function unbind() {
  const removed = await unbindConversationBindingRecord({ targetSessionKey: target, reason: 'r3-proof' });
  console.log('UNBOUND removed=%d', removed.length);
}

try { console.log('caps:', j(getConversationBindingCapabilities({ channel: conv.channel, accountId: conv.accountId }))); } catch (e) { console.log('caps err:', e.message); }

if (action === 'bind') { await bind(); resolve('after-bind'); }
else if (action === 'resolve') { resolve('cross-process'); console.log('listBySession count=%d', listSessionBindingRecords(target).length); }
else if (action === 'unbind') { await unbind(); resolve('after-unbind'); }
else { // cycle
  resolve('1-before');
  await bind();
  resolve('2-after-bind');
  console.log('listBySession count=%d (expect>=1)', listSessionBindingRecords(target).length);
  await unbind();
  resolve('3-after-unbind (expect null)');
}
