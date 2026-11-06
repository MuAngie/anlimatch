const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const port = 8790;
const files = {'/': ['index.html', 'text/html'], '/index.html': ['index.html', 'text/html'], '/styles.css': ['styles.css', 'text/css'], '/app.js': ['app.js', 'text/javascript']};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, {'Allow': 'GET, HEAD'}).end();
    return;
  }
  const asset = files[url.pathname];
  if (!asset) {
    res.writeHead(404, {'Content-Type': 'text/plain; charset=utf-8'}).end('文档资源不存在');
    return;
  }
  fs.readFile(path.join(__dirname, asset[0]), (error, data) => {
    if (error) {
      res.writeHead(500).end('无法读取文档资源');
      return;
    }
    res.writeHead(200, {
      'Content-Type': `${asset[1]}; charset=utf-8`,
      'Content-Length': data.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
      'X-Frame-Options': 'DENY'
    });
    res.end(req.method === 'HEAD' ? undefined : data);
  });
});
server.on('error', error => {
  console.error(error.code === 'EADDRINUSE' ? '端口 8790 已被占用。请检查已启动的说明服务。' : error.message);
  process.exitCode = 1;
});
server.listen(port, '127.0.0.1', () => console.log(`Project Handbook: http://127.0.0.1:${port}`));
