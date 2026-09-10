// Copy this Node standard-library helper into a benchmark; no package install is needed.
'use strict';
const fs = require('node:fs');
const events = new Set(['task_start', 'progress', 'input', 'output', 'failure', 'artifact', 'task_end']);

function emit(event, taskId, data = {}) {
  const path = process.env.AGENTAGON_EVENTS_PATH;
  if (!path) return false;
  if (!events.has(event) || typeof taskId !== 'string' || !taskId.length || [...taskId].length > 256) {
    throw new TypeError('invalid task event or task identity');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new TypeError('event data must be an object');
  const serialized = JSON.stringify({version: 1, event, task_id: taskId, at: new Date().toISOString(), data}, (_, value) => {
    if (typeof value === 'number' && !Number.isFinite(value)) throw new TypeError('event numbers must be finite');
    return value;
  });
  const line = Buffer.from(serialized + '\n', 'utf8');
  if (line.length > 65536) throw new RangeError('event exceeds 64 KiB; retain large output as an artifact');
  const fd = fs.openSync(path, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
  try {
    if (fs.writeSync(fd, line) !== line.length) throw new Error('partial task event write');
  } finally {
    fs.closeSync(fd);
  }
  return true;
}
module.exports = {emit};
