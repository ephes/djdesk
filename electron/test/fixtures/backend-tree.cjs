const { spawn } = require('node:child_process');

process.on('SIGTERM', () => {});
setInterval(() => {}, 1000);
if (process.argv[2] === 'helper') {
  process.send({ pid: process.pid });
} else {
  const helper = spawn(process.execPath, [__filename, 'helper'], {
    stdio: ['ignore', 'ignore', 'ignore', 'ipc'], windowsHide: true,
  });
  helper.once('message', (message) => process.send(message));
}
