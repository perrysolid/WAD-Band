'use strict';
const { createServer } = require('./app');

const port = Number(process.env.PORT || 8080);
const { server } = createServer();
server.listen(port, '0.0.0.0', () => {
  process.stdout.write(JSON.stringify({ level: 'info', msg: 'listening', port }) + '\n');
});
const stop = () => server.close(() => process.exit(0));
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
