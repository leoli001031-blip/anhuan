// Preserve Node TestsStream events as JSONL instead of parsing console text.
module.exports = async function* evidenceReporter(source) {
  for await (const event of source) {
    yield JSON.stringify(event, (_key, value) => value instanceof Error
      ? { name: value.name, message: value.message, stack: value.stack, ...value }
      : value) + '\n';
  }
  yield JSON.stringify({ type: 'evidence:complete' }) + '\n';
};
